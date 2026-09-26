"""装卸任务批量处理规则测试：覆盖成功、幂等、拦截、箱量保留与逐条独立性。"""
from __future__ import annotations

import unittest

from app.services.loading import MAX_PLAN_BOXES
from app.services.loading import LoadingService
from app.store import store


class LoadingBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.service = LoadingService()

    def _make(self, *, status: str, team: str | None = "甲班", plan: object = 100, done: object = 0):
        rows = store.rows("loading")
        entry = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "status": status,
            "任务状态": status,
            "任务编号": f"LOAD-T{len(rows) + 1}",
            "关联航次": "VOYA-T",
            "作业类型": "装船",
            "计划箱量": plan,
            "完成箱量": done,
            "作业班组": team if team is not None else "",
            "开始时间": "",
            "pending": status != "已完成",
            "abnormal": False,
        }
        rows.append(entry)
        return entry

    def test_batch_start_success_and_idempotent(self) -> None:
        ok = self._make(status="待开工", team="甲班", plan=300)

        result = self.service.batch_action("确认开工", [{"id": ok["id"]}])
        self.assertEqual(1, result["updated"])
        self.assertEqual(0, result["blocked"])
        self.assertEqual("作业中", store.find("loading", ok["id"])["status"])

        # 已经开工的任务重复提交只算一次：不报错、不重复计数、不动数据。
        again = self.service.batch_action("确认开工", [{"id": ok["id"]}])
        self.assertEqual(0, again["updated"])
        self.assertEqual(1, again["skipped"])
        self.assertEqual("noop", again["results"][0]["kind"])
        self.assertEqual("作业中", store.find("loading", ok["id"])["status"])

    def test_batch_start_missing_team_and_over_limit_picked_out(self) -> None:
        no_team = self._make(status="待开工", team="", plan=100)
        over_limit = self._make(status="待开工", team="乙班", plan=MAX_PLAN_BOXES + 1)
        good = self._make(status="待开工", team="丙班", plan=MAX_PLAN_BOXES)

        result = self.service.batch_action(
            "确认开工",
            [{"id": no_team["id"]}, {"id": over_limit["id"]}, {"id": good["id"]}],
        )
        kinds = {item["id"]: item["kind"] for item in result["results"]}
        self.assertEqual("rejected", kinds[no_team["id"]])
        self.assertEqual("rejected", kinds[over_limit["id"]])
        self.assertEqual("updated", kinds[good["id"]])

        reasons = {item["id"]: item["message"] for item in result["results"]}
        self.assertIn("作业班组", reasons[no_team["id"]])
        self.assertIn("上限", reasons[over_limit["id"]])
        # 被拦下的状态保持不变，合格的正常开工。
        self.assertEqual("待开工", store.find("loading", no_team["id"])["status"])
        self.assertEqual("待开工", store.find("loading", over_limit["id"])["status"])
        self.assertEqual("作业中", store.find("loading", good["id"])["status"])

    def test_batch_review_wrong_status_and_unknown_task_blocked(self) -> None:
        working = self._make(status="作业中", done=60)
        done = self._make(status="已完成", done=260)
        missing_id = 999999

        result = self.service.batch_action(
            "提交复核",
            [{"id": working["id"]}, {"id": done["id"]}, {"id": missing_id}],
        )
        self.assertEqual(1, result["updated"])
        self.assertEqual(2, result["blocked"])
        kinds = {item["id"]: item["kind"] for item in result["results"]}
        self.assertEqual("updated", kinds[working["id"]])
        self.assertEqual("rejected", kinds[done["id"]])
        self.assertEqual("rejected", kinds[missing_id])
        self.assertEqual("待复核", store.find("loading", working["id"])["status"])

    def test_completed_boxes_never_lost(self) -> None:
        done = self._make(status="已完成", done=260)
        review = self._make(status="待复核", done=318)
        working = self._make(status="作业中", done=180)

        # 已完工的任务被拦下时完成箱量必须保留。
        blocked = self.service.batch_action("提交复核", [{"id": done["id"]}])
        self.assertEqual(0, blocked["updated"])
        self.assertEqual(260, store.find("loading", done["id"])["完成箱量"])
        self.assertEqual("已完成", store.find("loading", done["id"])["status"])

        # 复核/完成流转不带完成箱量时原值保留；重复提交也保留。
        self.service.batch_action("提交复核", [{"id": working["id"]}])
        self.assertEqual(180, store.find("loading", working["id"])["完成箱量"])
        entry, _, kind = self.service.run_action(review["id"], "确认完成")
        self.assertEqual("updated", kind)
        self.assertEqual(318, entry["完成箱量"])
        _, message, again = self.service.run_action(done["id"], "确认完成")
        self.assertEqual("noop", again)
        self.assertEqual(260, store.find("loading", done["id"])["完成箱量"])
        self.assertIn("只计一次", message)

    def test_duplicate_ids_and_review_repeat_are_idempotent(self) -> None:
        working = self._make(status="作业中", done=50)

        once = self.service.batch_action(
            "提交复核", [{"id": working["id"]}, {"id": working["id"]}]
        )
        self.assertEqual(1, once["total"])
        self.assertEqual(1, once["updated"])
        twice = self.service.batch_action("提交复核", [{"id": working["id"]}])
        self.assertEqual(1, twice["skipped"])
        self.assertEqual("noop", twice["results"][0]["kind"])

    def test_invalid_batch_requests(self) -> None:
        with self.assertRaises(ValueError):
            self.service.batch_action("确认开工", [])
        with self.assertRaises(ValueError):
            self.service.batch_action("确认完成", [{"id": 1}])

    def test_single_action_enforces_state_machine(self) -> None:
        pending = self._make(status="待开工", team="", plan=100)
        entry, message, kind = self.service.run_action(pending["id"], "提交复核")
        self.assertIsNone(entry)
        self.assertEqual("rejected", kind)
        self.assertIn("作业中", message)
        self.assertEqual("待开工", store.find("loading", pending["id"])["status"])


if __name__ == "__main__":
    unittest.main()

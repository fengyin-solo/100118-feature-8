"""装卸任务业务规则：状态流转、字段校验与批量回执口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "loading"
REQUIRED_FIELDS = ["任务编号", "关联航次", "作业类型"]
OPTIONAL_FIELDS = ["计划箱量", "完成箱量", "作业班组", "开始时间"]
STATUS_ORDER = ["待开工", "作业中", "待复核", "已完成"]
ACTION_RULES = {"确认开工": "作业中", "提交复核": "待复核", "确认完成": "已完成"}
# 每个动作只允许从特定来源状态发起；不在其中且不是重复提交的，一律拦下说明缘由。
ACTION_SOURCES = {
    "确认开工": {"待开工"},
    "提交复核": {"作业中"},
    "确认完成": {"待复核"},
}
# 批量入口只受理开工与复核；确认完成仍走单条，避免成批误关单。
BATCH_ACTIONS = ["确认开工", "提交复核"]
BATCH_LIMIT = 200
MAX_PLAN_BOXES = 10000

NOOP_MESSAGES = {
    "确认开工": "任务已处于「作业中」，重复确认开工只计一次，状态保持不变",
    "提交复核": "任务已处于「待复核」，重复提交复核只计一次，状态保持不变",
    "确认完成": "任务已处于「已完成」，重复确认完成只计一次，状态保持不变",
}


def _parse_boxes(raw: Any) -> int | None:
    """箱量只接受正整数口径的数字；空值、小数、带文字的内容一律视为无法识别。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw if raw is not None else "").strip()
    if not text.isdigit():
        return None
    return int(text)


def _receipt(
    entry_id: int,
    task_no: str | None,
    *,
    ok: bool,
    kind: str,
    message: str,
    entry: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "taskNo": task_no,
        "ok": ok,
        "kind": kind,  # updated=本次生效 / noop=重复跳过 / rejected=拦截
        "message": message,
        "entry": entry,
    }


class LoadingService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry.setdefault("完成箱量", 0)
        entry["status"] = STATUS_ORDER[0]
        entry["任务状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str, str]:
        """执行单条动作，返回 (任务, 说明, 结果类型)；任务不存在或动作非法时任务为 None。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"装卸任务 {entry_id} 不存在或已归档", "rejected"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于装卸任务可执行范围", "rejected"
        kind, message = self._apply_action(entry, action, values or {})
        if kind == "rejected":
            return None, message, kind
        return entry, message, kind

    def batch_action(
        self,
        action: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """批量开工/复核：逐条独立处理，任一条被拦都不波及其它，每条都给回执。"""
        action = str(action or "").strip()
        if not items:
            raise ValueError("请至少勾选一条装卸任务再提交批量处理")
        if len(items) > BATCH_LIMIT:
            raise ValueError(f"单次最多批量处理 {BATCH_LIMIT} 条，请分批提交")
        if action not in BATCH_ACTIONS:
            raise ValueError("批量动作「%s」不在受理范围，仅支持确认开工、提交复核" % action)

        # 同一条任务在一次提交里重复出现没有意义，按 id 去重，只处理一次。
        ordered_ids: list[int] = []
        value_map: dict[int, dict[str, Any]] = {}
        seen: set[int] = set()
        for item in items:
            entry_id = int(item.get("id"))
            if entry_id in seen:
                continue
            seen.add(entry_id)
            ordered_ids.append(entry_id)
            values = item.get("values")
            value_map[entry_id] = values if isinstance(values, dict) else {}

        results: list[dict[str, Any]] = []
        for entry_id in ordered_ids:
            try:
                entry = store.find(MODULE, entry_id)
                if entry is None:
                    results.append(_receipt(
                        entry_id, None, ok=False, kind="rejected",
                        message=f"装卸任务 {entry_id} 不存在或已归档", entry=None,
                    ))
                    continue
                task_no = str(entry.get("任务编号") or f"#{entry_id}")
                kind, message = self._apply_action(entry, action, value_map[entry_id])
                snapshot = None if kind == "rejected" else dict(entry)
                results.append(_receipt(
                    entry_id, task_no, ok=kind != "rejected",
                    kind=kind, message=message, entry=snapshot,
                ))
            except Exception as exc:  # 单条处理异常绝不能让整批失败
                results.append(_receipt(
                    entry_id, None, ok=False, kind="rejected",
                    message=f"处理失败：{exc}", entry=None,
                ))

        updated = sum(1 for item in results if item["kind"] == "updated")
        skipped = sum(1 for item in results if item["kind"] == "noop")
        blocked = sum(1 for item in results if item["kind"] == "rejected")
        message = (
            f"批量{action}完成：成功 {updated} 条，重复跳过 {skipped} 条，拦截 {blocked} 条"
        )
        return {
            "ok": updated + skipped > 0,
            "action": action,
            "total": len(results),
            "updated": updated,
            "skipped": skipped,
            "blocked": blocked,
            "message": message,
            "results": results,
        }

    def _apply_action(
        self,
        entry: dict[str, Any],
        action: str,
        values: dict[str, Any],
    ) -> tuple[str, str]:
        """单条状态流转规则，返回 (结果类型, 说明)；rejected 时不写入任何字段。"""
        target = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        task_no = str(entry.get("任务编号") or f"#{entry.get('id')}")

        # 已在目标状态：重复提交幂等，只计一次，不动数据。
        if current == target:
            return "noop", NOOP_MESSAGES[action]
        if current not in ACTION_SOURCES[action]:
            allowed = "、".join(sorted(ACTION_SOURCES[action]))
            return (
                "rejected",
                f"任务当前为「{current}」，不能{action}（仅{allowed}状态可执行），已跳过",
            )

        # 开工前置条件：作业班组必须已指派，计划箱量必须可识别且不超单任务上限。
        if action == "确认开工":
            if not str(entry.get("作业班组") or "").strip():
                return "rejected", "作业班组未填写，请先指派作业班组后再开工"
            plan = _parse_boxes(entry.get("计划箱量"))
            raw_plan = entry.get("计划箱量")
            if plan is None:
                return "rejected", f"计划箱量「{raw_plan if raw_plan not in (None, '') else '空'}」不是有效整数，无法核校作业上限"
            if plan <= 0:
                return "rejected", "计划箱量必须为大于 0 的整数，请核对计划后再开工"
            if plan > MAX_PLAN_BOXES:
                return (
                    "rejected",
                    f"计划箱量 {plan} 箱超过单任务上限 {MAX_PLAN_BOXES} 箱，请先调整计划再开工",
                )

        # 复核/完成允许顺带回填完成箱量；未提交时保留原值，任何流转都不清空该字段。
        if action in ("提交复核", "确认完成"):
            done = _parse_boxes(values.get("完成箱量"))
            if done is not None:
                entry["完成箱量"] = done

        entry["status"] = target
        entry["任务状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = False
        return "updated", f"装卸任务 {task_no} 已{action}"

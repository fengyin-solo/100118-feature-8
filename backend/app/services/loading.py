"""装卸任务业务规则：状态流转、字段校验与筛选口径都收在这里。

状态只能沿 待开工 → 作业中 → 待复核 → 已完成 单向推进：
- 已经处于目标状态之后的任务再次提交同一动作，按重复提交处理（幂等，只算一次）；
- 跳着走（比如待开工直接提交复核）会被拦下并说明缘由；
- 确认开工要求作业班组已填、计划箱量是不超过上限的正数；
- 任何动作都只改状态字段，完成箱量等既有数据原样保留。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "loading"
REQUIRED_FIELDS = ["任务编号", "关联航次", "作业类型"]
STATUS_ORDER = ["待开工", "作业中", "待复核", "已完成"]
ACTION_RULES = {"确认开工": "作业中", "提交复核": "待复核", "确认完成": "已完成"}
NEGATIVE_ACTIONS = []

# 单条装卸任务计划箱量上限，超过上限的任务不允许开工。
PLANNED_BOX_LIMIT = 1000

# 批量动作回执的三种结局。
OUTCOME_APPLIED = "applied"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_BLOCKED = "blocked"


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
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for optional in ("计划箱量", "完成箱量", "作业班组", "开始时间"):
            if optional in values:
                entry[optional] = values.get(optional)
        entry["status"] = STATUS_ORDER[0]
        entry["任务状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """对单条任务执行动作；返回的条目为 None 表示动作未生效，说明里给出缘由。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"装卸任务 {entry_id} 不存在或已归档"
        outcome, message = self._apply_action(entry, action)
        if outcome == OUTCOME_BLOCKED:
            return None, message
        return entry, message

    def run_batch_action(
        self, entry_ids: list[int], action: str
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """批量执行同一动作：逐条处理、逐条给回执，单条被拦下不影响其它任务。

        重复提交的 id 只处理一次，回执按去重后的提交顺序返回，便于和勾选行对齐。
        """
        if action not in ACTION_RULES:
            raise ValueError(f"动作「{action}」不属于装卸任务可执行范围")
        results: list[dict[str, Any]] = []
        counters = {OUTCOME_APPLIED: 0, OUTCOME_DUPLICATE: 0, OUTCOME_BLOCKED: 0}
        for entry_id in dict.fromkeys(entry_ids):
            entry = store.find(MODULE, entry_id)
            if entry is None:
                results.append({
                    "id": entry_id,
                    "task_no": None,
                    "outcome": OUTCOME_BLOCKED,
                    "ok": False,
                    "duplicate": False,
                    "message": f"装卸任务 {entry_id} 不存在或已归档",
                    "entry": None,
                })
                counters[OUTCOME_BLOCKED] += 1
                continue
            outcome, message = self._apply_action(entry, action)
            counters[outcome] += 1
            results.append({
                "id": entry_id,
                "task_no": entry.get("任务编号"),
                "outcome": outcome,
                "ok": outcome != OUTCOME_BLOCKED,
                "duplicate": outcome == OUTCOME_DUPLICATE,
                "message": message,
                "entry": entry,
            })
        return results, counters

    def _apply_action(self, entry: dict[str, Any], action: str) -> tuple[str, str]:
        """在任务上落地一次动作，返回 (结局, 说明)。任务数据在此处统一更新。"""
        target = ACTION_RULES.get(action)
        if target is None:
            return OUTCOME_BLOCKED, f"动作「{action}」不属于装卸任务可执行范围"
        current = str(entry.get("status") or "")
        if current not in STATUS_ORDER:
            return OUTCOME_BLOCKED, f"当前状态「{current}」不在允许的状态序列里"
        current_index = STATUS_ORDER.index(current)
        target_index = STATUS_ORDER.index(target)

        # 已经到达或越过目标状态：重复提交只算一次，不重复推进。
        if current_index >= target_index:
            if current == target:
                return (
                    OUTCOME_DUPLICATE,
                    f"任务已是「{current}」状态，{action}此前已提交，本次按重复提交处理",
                )
            return (
                OUTCOME_DUPLICATE,
                f"任务已推进到「{current}」，{action}已在流程中完成，无需重复提交",
            )

        # 只允许沿状态序列相邻推进，跳步一律拦下。
        if current_index + 1 != target_index:
            return (
                OUTCOME_BLOCKED,
                f"任务当前为「{current}」，需先完成「{STATUS_ORDER[current_index + 1]}」阶段，不能直接{action}",
            )

        # 开工前的硬性前置：作业班组已填、计划箱量不超过上限。
        blockers = self._start_blockers(entry)
        if blockers:
            return OUTCOME_BLOCKED, "；".join(blockers)

        entry["status"] = target
        entry["任务状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return OUTCOME_APPLIED, f"装卸任务已{action}"

    def _start_blockers(self, entry: dict[str, Any]) -> list[str]:
        """确认开工的前置校验：作业班组没填或计划箱量超过上限的任务单独挑出。"""
        blockers: list[str] = []
        if not str(entry.get("作业班组") or "").strip():
            blockers.append("作业班组未填写，不能开工")
        planned = _as_int(entry.get("计划箱量"))
        if planned is None or planned <= 0:
            blockers.append("计划箱量不是有效正数，不能开工")
        elif planned > PLANNED_BOX_LIMIT:
            blockers.append(f"计划箱量 {planned} 超过单任务上限 {PLANNED_BOX_LIMIT}，不能开工")
        return blockers


def _as_int(value: Any) -> int | None:
    """把计划箱量这类可能是字符串或带小数的字段宽松转成整数；转不了就返回 None。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

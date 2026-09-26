"""装卸任务接口：维护装卸任务，覆盖确认开工、提交复核、确认完成等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BatchActionPayload,
    BatchActionResult,
    EntryPayload,
    PageResult,
)
from app.services.loading import LoadingService

router = APIRouter(prefix="/api/loading", tags=["装卸任务"])

service = LoadingService()

LIST_FIELDS = ["任务编号", "关联航次", "作业类型", "计划箱量", "完成箱量", "作业班组", "开始时间", "任务状态"]
STATUSES = ["待开工", "作业中", "待复核", "已完成"]
BATCH_ACTIONS = ["确认开工", "提交复核"]
BATCH_LIMIT = 200


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="待开工、作业中、待复核、已完成"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号与状态过滤装卸任务列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出装卸任务清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "loading", "total": total, "items": items}


@router.post("/batch/actions", response_model=BatchActionResult)
def run_batch_action(payload: BatchActionPayload) -> BatchActionResult:
    """批量确认开工或批量提交复核。

    勾选的任务逐条处理并给出回执：成功的生效，重复提交的只算一次，
    因班组缺失、箱量超限或状态不符被拦下的单条说明缘由，不影响其它任务。
    """
    action = str(payload.action or "").strip()
    if action not in BATCH_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"批量操作仅支持：{'、'.join(BATCH_ACTIONS)}",
        )
    entry_ids = [int(value) for value in payload.entry_ids]
    if not entry_ids:
        raise HTTPException(status_code=400, detail="请至少勾选一条装卸任务后再提交")
    if len(entry_ids) > BATCH_LIMIT:
        raise HTTPException(status_code=400, detail=f"单次最多处理 {BATCH_LIMIT} 条任务，请分批提交")
    results, counters = service.run_batch_action(entry_ids, action)
    total = len(results)
    summary = f"批量{action}完成：成功 {counters['applied']} 条"
    if counters["duplicate"]:
        summary += f"，重复提交 {counters['duplicate']} 条（只算一次）"
    if counters["blocked"]:
        summary += f"，拦下 {counters['blocked']} 条（见回执缘由）"
    return BatchActionResult(
        action=action,
        total=total,
        applied=counters["applied"],
        duplicated=counters["duplicate"],
        blocked=counters["blocked"],
        results=results,
        message=summary,
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条装卸任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"装卸任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条装卸任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="装卸任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条装卸任务执行确认开工、提交复核、确认完成；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)

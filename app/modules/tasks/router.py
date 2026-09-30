from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_async_task import DEADLINE_FIELD, IDEMPOTENCY_KEY_FIELD, TASK_ID_FIELD
from app.common.task_control import request_task_cancel
from app.common.task_models import AsyncTaskExecutionEntity
from app.database import get_db
from app.infrastructure.redis.redis_service import RedisService, get_redis
from app.modules.auth.dependencies import get_current_user_id

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


async def _owned(task_id: str, user_id: int, db: AsyncSession) -> AsyncTaskExecutionEntity:
    task = await db.scalar(select(AsyncTaskExecutionEntity).where(
        AsyncTaskExecutionEntity.task_id == task_id,
        AsyncTaskExecutionEntity.user_id == user_id,
    ))
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("")
async def list_tasks(user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(AsyncTaskExecutionEntity)
        .where(AsyncTaskExecutionEntity.user_id == user_id)
        .order_by(AsyncTaskExecutionEntity.created_at.desc()).limit(100))).all()
    return {"items": rows}


@router.get("/{task_id}")
async def get_task(task_id: str, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await _owned(task_id, user_id, db)


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    task = await _owned(task_id, user_id, db)
    if task.status not in {"PENDING", "PROCESSING", "RETRYING"}:
        raise HTTPException(status_code=409, detail=f"当前任务状态 {task.status} 不支持取消")
    redis = await get_redis()
    await request_task_cancel(RedisService(redis), task_id)
    task.status = "CANCELLED"
    await db.commit()
    return {"task_id": task_id, "status": "CANCELLED"}


@router.post("/{task_id}/replay")
async def replay_task(task_id: str, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    task = await _owned(task_id, user_id, db)
    if task.status not in {"FAILED", "TIMEOUT", "DEAD_LETTER"}:
        raise HTTPException(status_code=409, detail="仅失败、超时或死信任务可重放")
    redis = await get_redis()
    fields = {str(k): str(v) for k, v in (task.payload or {}).items()}
    deadline = task.deadline_at or (datetime.now(timezone.utc) + timedelta(minutes=5))
    fields.update({TASK_ID_FIELD: task_id, IDEMPOTENCY_KEY_FIELD: task.idempotency_key, DEADLINE_FIELD: str(int(deadline.timestamp()))})
    await RedisService(redis).xadd(task.stream_key, fields, maxlen=10000)
    task.status = "RETRYING"
    task.next_retry_at = None
    await db.commit()
    return {"task_id": task_id, "status": "RETRYING"}

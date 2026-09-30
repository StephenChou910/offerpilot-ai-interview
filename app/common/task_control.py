"""Small, transport-agnostic controls for asynchronous task lifecycle."""

from app.common.base_async_task import task_cancel_key
from app.infrastructure.redis.redis_service import RedisService


async def request_task_cancel(
    redis_service: RedisService, task_id: str, *, ttl_seconds: int = 86_400
) -> None:
    """Mark a task for cancellation before its worker starts processing it."""
    await redis_service.set(task_cancel_key(task_id), "1", ex=ttl_seconds)


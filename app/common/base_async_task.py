import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from app.common.model import AsyncTaskStatus
from app.infrastructure.redis.redis_service import RedisService
from app.common.task_models import AsyncTaskExecutionEntity
from app.common.request_context import get_request_id, request_id_context

logger = logging.getLogger(__name__)

TASK_TIMEOUT_SECONDS = 300  # 5 分钟超时
TASK_ID_FIELD = "_task_id"
IDEMPOTENCY_KEY_FIELD = "_idempotency_key"
DEADLINE_FIELD = "_deadline"
CREATED_AT_FIELD = "_created_at"
CANCEL_KEY_PREFIX = "async-task:cancel:"


def task_cancel_key(task_id: str) -> str:
    return f"{CANCEL_KEY_PREFIX}{task_id}"


class StreamTaskProducer:
    """通用 Redis Stream 任务生产者。"""

    def __init__(self, redis_service: RedisService, stream_key: str):
        self._redis = redis_service
        self._stream_key = stream_key

    async def send_task(
        self,
        fields: dict[str, str],
        maxlen: int = 1000,
        *,
        idempotency_key: str | None = None,
        deadline_seconds: int = TASK_TIMEOUT_SECONDS,
        user_id: int | None = None,
        workflow_id: str | None = None,
        step_id: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Publish a versioned task envelope and return its task id.

        Business fields remain unchanged; underscore-prefixed fields are orchestration
        metadata consumed by workers and safe for existing handlers to ignore.
        """
        task_id = uuid.uuid4().hex
        envelope = dict(fields)
        envelope[TASK_ID_FIELD] = task_id
        envelope[IDEMPOTENCY_KEY_FIELD] = idempotency_key or task_id
        envelope[DEADLINE_FIELD] = str(int(datetime.now(timezone.utc).timestamp()) + deadline_seconds)
        envelope[CREATED_AT_FIELD] = datetime.now(timezone.utc).isoformat()
        if user_id is not None:
            envelope["_user_id"] = str(user_id)
        if workflow_id:
            envelope["_workflow_id"] = workflow_id
        if step_id:
            envelope["_step_id"] = step_id
        envelope["_request_id"] = request_id or get_request_id()
        try:
            await self._redis.xadd(self._stream_key, envelope, maxlen=maxlen)
            logger.info("已发送任务: stream=%s, task_id=%s, idempotency_key=%s", self._stream_key, task_id, envelope[IDEMPOTENCY_KEY_FIELD])
            return task_id
        except Exception as e:
            logger.error("发送任务失败: stream=%s, error=%s", self._stream_key, e)
            raise


class StreamTaskHandler(ABC):
    """通用 Redis Stream 任务处理器基类。

    子类需要实现:
        field_name: 消息中的主键字段名
        process(): 实际业务处理逻辑
        update_status(): 更新任务状态的方法
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], stream_key: str | None = None):
        self._session_factory = session_factory
        self._stream_key = stream_key or self.__class__.__name__

    @property
    @abstractmethod
    def field_name(self) -> str:
        """消息中标识任务的字段名，如 'resumeId'、'sessionId'。"""

    @abstractmethod
    async def process(self, db: AsyncSession, key_value: str) -> None:
        """实际业务处理逻辑，在单个事务中执行。"""

    @abstractmethod
    async def update_status(
        self, db: AsyncSession, key_value: str, status: AsyncTaskStatus, error: str | None = None
    ) -> None:
        """更新任务状态。"""

    async def handle(self, fields: dict[str, str]) -> None:
        request_token = request_id_context.set(fields.get("_request_id", "-"))
        raw = fields.get(self.field_name)
        if not raw:
            logger.warning("忽略无效任务，缺少 %s: %s", self.field_name, fields)
            request_id_context.reset(request_token)
            return

        task_id = fields.get(TASK_ID_FIELD, "legacy")
        idempotency_key = fields.get(IDEMPOTENCY_KEY_FIELD, task_id)
        deadline = _parse_deadline(fields.get(DEADLINE_FIELD))
        timeout = max(0.1, min(TASK_TIMEOUT_SECONDS, deadline - datetime.now(timezone.utc).timestamp())) if deadline else TASK_TIMEOUT_SECONDS

        try:
          async with self._session_factory() as db:
            try:
                claimed = await self._claim_task(
                    db, task_id, idempotency_key, raw, fields, deadline
                )
                if not claimed:
                    logger.info("跳过重复任务: task_id=%s, idempotency_key=%s", task_id, idempotency_key)
                    return
                logger.info("开始处理任务: task_id=%s, field=%s, value=%s, timeout=%.1fs", task_id, self.field_name, raw, timeout)
                await self._set_task_status(db, task_id, "PROCESSING")
                await self.update_status(db, raw, AsyncTaskStatus.PROCESSING)
                # Do not keep a database transaction (or row lock) open while
                # an external LLM request is running for minutes.
                await db.commit()
                await asyncio.wait_for(self.process(db, raw), timeout=timeout)
                await self.update_status(db, raw, AsyncTaskStatus.COMPLETED)
                await self._set_task_status(db, task_id, "COMPLETED")
                await db.commit()
                logger.info("任务处理完成: %s=%s", self.field_name, raw)
            except asyncio.TimeoutError:
                await db.rollback()
                error_msg = f"任务处理超时（task_id={task_id}, timeout={timeout:.1f}秒）"
                logger.error("任务超时: task_id=%s, %s=%s", task_id, self.field_name, raw)
                async with self._session_factory() as failed_db:
                    await self._set_task_status(failed_db, task_id, "TIMEOUT", error_msg)
                    await self.update_status(failed_db, raw, AsyncTaskStatus.FAILED, error_msg)
                    await failed_db.commit()
                raise
            except Exception as e:
                await db.rollback()
                logger.error("任务处理失败: %s=%s, error=%s", self.field_name, raw, e)
                async with self._session_factory() as failed_db:
                    await self._set_task_status(failed_db, task_id, "FAILED", str(e))
                    await self.update_status(failed_db, raw, AsyncTaskStatus.FAILED, str(e))
                    await failed_db.commit()
                raise
        finally:
            request_id_context.reset(request_token)

    async def cancel(self, fields: dict[str, str]) -> None:
        """Persist cancellation when a worker observes a cancel marker."""
        task_id = fields.get(TASK_ID_FIELD)
        if not task_id:
            return
        async with self._session_factory() as db:
            await self._set_task_status(db, task_id, "CANCELLED", "任务被取消")
            await db.commit()

    async def dead_letter(self, fields: dict[str, str], reason: str) -> None:
        """Persist terminal dead-letter state for an exhausted message."""
        task_id = fields.get(TASK_ID_FIELD)
        if not task_id:
            return
        async with self._session_factory() as db:
            await self._set_task_status(db, task_id, "DEAD_LETTER", reason)
            await db.commit()
    async def _claim_task(
        self,
        db: AsyncSession,
        task_id: str,
        idempotency_key: str,
        business_key: str,
        fields: dict[str, str],
        deadline: float | None,
    ) -> bool:
        # Lightweight test sessions and legacy integrations may not expose the
        # SQLAlchemy scalar API; business task execution remains compatible.
        if not hasattr(db, "scalar"):
            return True
        existing = await db.scalar(
            select(AsyncTaskExecutionEntity).where(
                AsyncTaskExecutionEntity.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            # A successful or currently running task is idempotently ignored.
            # Failed/time-out tasks are legitimate retries of the same work item.
            if existing.status in {"PENDING", "PROCESSING", "COMPLETED"}:
                return False
            existing.status = "PENDING"
            existing.retry_count += 1
            existing.error_message = None
            existing.started_at = datetime.now(timezone.utc)
            existing.finished_at = None
            await db.flush()
            return True
        entity = AsyncTaskExecutionEntity(
            task_id=task_id,
            idempotency_key=idempotency_key,
            stream_key=self._stream_key,
            business_key=business_key,
            workflow_id=fields.get("_workflow_id"),
            step_id=fields.get("_step_id"),
            user_id=_parse_int(fields.get("_user_id")),
            request_id=fields.get("_request_id"),
            status="PENDING",
            deadline_at=datetime.fromtimestamp(deadline, tz=timezone.utc) if deadline else None,
            payload={k: v for k, v in fields.items() if not k.startswith("_")},
            started_at=datetime.now(timezone.utc),
        )
        db.add(entity)
        try:
            await db.flush()
            return True
        except IntegrityError:
            await db.rollback()
            return False

    @staticmethod
    async def _set_task_status(
        db: AsyncSession, task_id: str, status: str, error: str | None = None
    ) -> None:
        if not hasattr(db, "scalar"):
            return
        entity = await db.scalar(
            select(AsyncTaskExecutionEntity).where(AsyncTaskExecutionEntity.task_id == task_id)
        )
        if entity is None:
            return
        entity.status = status
        entity.error_message = error[:2000] if error else None
        if status in {"FAILED", "TIMEOUT"}:
            entity.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=min(300, 2 ** min(entity.retry_count, 8)))
        elif status in {"PROCESSING", "COMPLETED", "CANCELLED", "DEAD_LETTER"}:
            entity.next_retry_at = None
        if status in {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "DEAD_LETTER"}:
            entity.finished_at = datetime.now(timezone.utc)
        await db.flush()


def _parse_deadline(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("任务 deadline 无效，将使用默认超时: %s", value)
        return None


def _parse_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

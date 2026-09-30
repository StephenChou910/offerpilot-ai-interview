"""Durable execution metadata shared by Redis Stream workers."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AsyncTaskExecutionEntity(Base):
    __tablename__ = "async_task_executions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_async_task_idempotency_key"),
        Index("idx_async_task_stream_status", "stream_key", "status"),
        Index("idx_async_task_created_at", "created_at"),
        Index("idx_async_task_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    stream_key: Mapped[str] = mapped_column(String(255), nullable=False)
    business_key: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_letter_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

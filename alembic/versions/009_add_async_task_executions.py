"""add durable async task execution metadata

Revision ID: 009_async_task_executions
Revises: a1b2c3d4e5f6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009_async_task_executions"
down_revision: Union[str, None] = "008_scope_kb_hash_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "async_task_executions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("stream_key", sa.String(length=255), nullable=False),
        sa.Column("business_key", sa.String(length=255), nullable=False),
        sa.Column("workflow_id", sa.String(length=64), nullable=True),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_letter_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_async_task_idempotency_key"),
    )
    op.create_index("idx_async_task_stream_status", "async_task_executions", ["stream_key", "status"])
    op.create_index("idx_async_task_created_at", "async_task_executions", ["created_at"])
    op.create_index("idx_async_task_user_status", "async_task_executions", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_async_task_created_at", table_name="async_task_executions")
    op.drop_index("idx_async_task_stream_status", table_name="async_task_executions")
    op.drop_index("idx_async_task_user_status", table_name="async_task_executions")
    op.drop_table("async_task_executions")

"""add durable LLM call audit records"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "010_llm_call_audits"
down_revision: Union[str, None] = "009_async_task_executions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_call_audits",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_llm_audit_request_created", "llm_call_audits", ["request_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_llm_audit_request_created", table_name="llm_call_audits")
    op.drop_table("llm_call_audits")

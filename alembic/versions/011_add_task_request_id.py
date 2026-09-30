"""persist request id on async task executions"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "011_task_request_id"
down_revision: Union[str, None] = "010_llm_call_audits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("async_task_executions", sa.Column("request_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("async_task_executions", "request_id")

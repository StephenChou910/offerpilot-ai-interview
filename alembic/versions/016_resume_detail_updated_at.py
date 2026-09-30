"""add updated timestamps to structured resume detail tables"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "016_resume_detail_updated_at"
down_revision: Union[str, None] = "015_resume_source_locations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("resume_work_experiences", "resume_projects", "resume_skills"):
        op.add_column(table, sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False))


def downgrade() -> None:
    for table in ("resume_skills", "resume_projects", "resume_work_experiences"):
        op.drop_column(table, "updated_at")

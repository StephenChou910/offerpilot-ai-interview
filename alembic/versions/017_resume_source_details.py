"""add source type and excerpts to structured resume records"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "017_resume_source_details"
down_revision: Union[str, None] = "016_resume_detail_updated_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("resume_versions", "resume_work_experiences", "resume_projects", "resume_skills"):
        op.add_column(table, sa.Column("source_type", sa.String(32), server_default="text", nullable=False))
        op.add_column(table, sa.Column("source_excerpt", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in ("resume_skills", "resume_projects", "resume_work_experiences", "resume_versions"):
        op.drop_column(table, "source_excerpt")
        op.drop_column(table, "source_type")

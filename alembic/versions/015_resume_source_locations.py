"""add source page and locator metadata to structured resume records"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "015_resume_source_locations"
down_revision: Union[str, None] = "014_resume_project_skills"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("resume_versions", "resume_work_experiences", "resume_projects", "resume_skills"):
        op.add_column(table, sa.Column("source_page", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("source_locator", sa.String(200), nullable=True))


def downgrade() -> None:
    for table in ("resume_skills", "resume_projects", "resume_work_experiences", "resume_versions"):
        op.drop_column(table, "source_locator")
        op.drop_column(table, "source_page")

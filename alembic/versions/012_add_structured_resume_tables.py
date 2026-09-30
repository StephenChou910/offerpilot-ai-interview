"""add versioned structured resume tables"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "012_structured_resume"
down_revision: Union[str, None] = "011_task_request_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("resume_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("resume_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("source_text", sa.Text()),
        sa.Column("extraction_method", sa.String(32), nullable=False), sa.Column("confidence", sa.Float()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("resume_id", "version_no", name="uq_resume_version_no"))
    op.create_table("resume_profiles",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("version_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("summary", sa.Text()), sa.Column("experience_level", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["resume_versions.id"], ondelete="CASCADE"))
    common = [sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("profile_id", sa.BigInteger(), nullable=False),
              sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"), sa.Column("source_text", sa.Text()), sa.Column("confidence", sa.Float()),
              sa.ForeignKeyConstraint(["profile_id"], ["resume_profiles.id"], ondelete="CASCADE")]
    op.create_table("resume_work_experiences", *common, sa.Column("company", sa.String(300), nullable=False), sa.Column("title", sa.String(200)), sa.Column("start_date", sa.String(32)), sa.Column("end_date", sa.String(32)), sa.Column("description", sa.Text()))
    op.create_table("resume_projects", *common, sa.Column("name", sa.String(300), nullable=False), sa.Column("role", sa.String(300)), sa.Column("description", sa.Text()), sa.Column("highlights", sa.Text()))
    op.create_table("resume_skills", sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("profile_id", sa.BigInteger(), nullable=False), sa.Column("name", sa.String(200), nullable=False), sa.Column("proficiency", sa.String(64)), sa.Column("context", sa.Text()), sa.Column("source_text", sa.Text()), sa.Column("confidence", sa.Float()), sa.ForeignKeyConstraint(["profile_id"], ["resume_profiles.id"], ondelete="CASCADE"), sa.UniqueConstraint("profile_id", "name", name="uq_resume_profile_skill"))
    for table in ("resume_work_experiences", "resume_projects", "resume_skills"):
        op.create_index(f"idx_{table}_profile_id", table, ["profile_id"])


def downgrade() -> None:
    for table in ("resume_skills", "resume_projects", "resume_work_experiences"):
        op.drop_index(f"idx_{table}_profile_id", table_name=table); op.drop_table(table)
    op.drop_table("resume_profiles"); op.drop_table("resume_versions")

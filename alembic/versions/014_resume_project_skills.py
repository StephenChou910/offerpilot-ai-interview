"""add project skill relations for structured resumes"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "014_resume_project_skills"
down_revision: Union[str, None] = "013_resume_text_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resume_project_skills",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("skill_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["resume_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["resume_skills.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "skill_id", name="uq_resume_project_skill"),
    )
    op.create_index("idx_resume_project_skill_project", "resume_project_skills", ["project_id"])
    op.create_index("idx_resume_project_skill_skill", "resume_project_skills", ["skill_id"])


def downgrade() -> None:
    op.drop_index("idx_resume_project_skill_skill", table_name="resume_project_skills")
    op.drop_index("idx_resume_project_skill_project", table_name="resume_project_skills")
    op.drop_table("resume_project_skills")

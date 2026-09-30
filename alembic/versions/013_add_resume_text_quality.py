"""add resume text quality metadata"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "013_resume_text_quality"
down_revision: Union[str, None] = "012_structured_resume"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("resumes", sa.Column("text_quality_json", sa.Text(), nullable=True))
    op.add_column("resumes", sa.Column("extraction_method", sa.String(32), nullable=False, server_default="text"))


def downgrade() -> None:
    op.drop_column("resumes", "extraction_method")
    op.drop_column("resumes", "text_quality_json")

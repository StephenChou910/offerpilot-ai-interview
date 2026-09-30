from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, Float, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.model import AsyncTaskStatus
from app.models.base import Base


class ResumeEntity(Base):
    __tablename__ = "resumes"
    __table_args__ = (
        Index("idx_resume_user_id", "user_id"),
        Index("idx_resume_user_file_hash", "user_id", "file_hash", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    content_type: Mapped[str | None] = mapped_column(String(200))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    storage_url: Mapped[str | None] = mapped_column(String(1000))
    resume_text: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    analyze_status: Mapped[AsyncTaskStatus] = mapped_column(
        Enum(AsyncTaskStatus), default=AsyncTaskStatus.PENDING, nullable=False
    )
    analyze_error: Mapped[str | None] = mapped_column(String(500))
    text_quality_json: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False, default="text")

    analyses: Mapped[list["ResumeAnalysisEntity"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan", lazy="selectin"
    )
    versions: Mapped[list["ResumeVersionEntity"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan", lazy="selectin"
    )

    def increment_access_count(self) -> None:
        self.access_count += 1
        self.last_accessed_at = datetime.now()


class ResumeAnalysisEntity(Base):
    __tablename__ = "resume_analyses"
    __table_args__ = (Index("idx_analysis_resume_id", "resume_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False)
    overall_score: Mapped[int | None] = mapped_column(Integer)
    content_score: Mapped[int | None] = mapped_column(Integer)
    structure_score: Mapped[int | None] = mapped_column(Integer)
    skill_match_score: Mapped[int | None] = mapped_column(Integer)
    expression_score: Mapped[int | None] = mapped_column(Integer)
    project_score: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    strengths_json: Mapped[str | None] = mapped_column(Text)
    suggestions_json: Mapped[str | None] = mapped_column(Text)
    profile_json: Mapped[str | None] = mapped_column(Text)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    resume: Mapped["ResumeEntity"] = relationship(back_populates="analyses")


class ResumeVersionEntity(Base):
    __tablename__ = "resume_versions"
    __table_args__ = (UniqueConstraint("resume_id", "version_no", name="uq_resume_version_no"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    confidence: Mapped[float | None] = mapped_column(Float)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_locator: Mapped[str | None] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    resume: Mapped["ResumeEntity"] = relationship(back_populates="versions")
    profile: Mapped["ResumeProfileEntity | None"] = relationship(back_populates="version", cascade="all, delete-orphan", uselist=False)


class ResumeProfileEntity(Base):
    __tablename__ = "resume_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("resume_versions.id", ondelete="CASCADE"), nullable=False, unique=True)
    summary: Mapped[str | None] = mapped_column(Text)
    experience_level: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    version: Mapped["ResumeVersionEntity"] = relationship(back_populates="profile")
    work_experiences: Mapped[list["ResumeWorkExperienceEntity"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    projects: Mapped[list["ResumeProjectEntity"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    skills: Mapped[list["ResumeSkillEntity"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class ResumeWorkExperienceEntity(Base):
    __tablename__ = "resume_work_experiences"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("resume_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    company: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[str | None] = mapped_column(String(32))
    end_date: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    source_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_locator: Mapped[str | None] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ResumeProjectEntity(Base):
    __tablename__ = "resume_projects"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("resume_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    highlights: Mapped[str | None] = mapped_column(Text)
    source_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_locator: Mapped[str | None] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    skills: Mapped[list["ResumeSkillEntity"]] = relationship(
        secondary="resume_project_skills", lazy="selectin"
    )


class ResumeSkillEntity(Base):
    __tablename__ = "resume_skills"
    __table_args__ = (UniqueConstraint("profile_id", "name", name="uq_resume_profile_skill"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("resume_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    proficiency: Mapped[str | None] = mapped_column(String(64))
    context: Mapped[str | None] = mapped_column(Text)
    source_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_locator: Mapped[str | None] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ResumeProjectSkillEntity(Base):
    __tablename__ = "resume_project_skills"
    __table_args__ = (UniqueConstraint("project_id", "skill_id", name="uq_resume_project_skill"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("resume_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("resume_skills.id", ondelete="CASCADE"), nullable=False, index=True)

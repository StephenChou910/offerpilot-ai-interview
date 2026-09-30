from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.model import AsyncTaskStatus


class ScoreDetail(BaseModel):
    content_score: int = 0
    structure_score: int = 0
    skill_match_score: int = 0
    expression_score: int = 0
    project_score: int = 0


class Suggestion(BaseModel):
    category: str
    priority: str
    issue: str
    recommendation: str


class ProjectInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=300)
    role: str = Field(default="", max_length=300)
    tech_stack: list[str] = Field(default_factory=list, max_length=50)
    description: str = Field(default="", max_length=10000)
    highlights: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("name", "role", "description", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        return str(value or "").strip()


class TechStack(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=200)
    proficiency: str = Field(default="", max_length=64)
    context: str = Field(default="", max_length=5000)


class WorkExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")
    company: str = Field(min_length=1, max_length=300)
    title: str = Field(default="", max_length=200)
    start_date: str = Field(default="", max_length=32)
    end_date: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=10000)


class ResumeProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    projects: list[ProjectInfo] = Field(default_factory=list, max_length=100)
    tech_stacks: list[TechStack] = Field(default_factory=list, max_length=200)
    experience_level: str = Field(default="unknown", max_length=64)
    has_projects: bool = False
    summary: str = Field(default="", max_length=10000)
    work_experiences: list[WorkExperience] = Field(default_factory=list, max_length=100)


class StructuredResumeUpdate(BaseModel):
    """用户确认/纠正后的结构化简历内容。"""
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(default="", max_length=10000)
    experience_level: str = Field(default="unknown", max_length=64)
    projects: list[ProjectInfo] = Field(default_factory=list, max_length=100)
    skills: list[TechStack] = Field(default_factory=list, max_length=200)
    work_experiences: list[WorkExperience] = Field(default_factory=list, max_length=100)


class ResumeAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    overall_score: int = Field(ge=0, le=100)
    score_detail: ScoreDetail
    summary: str
    strengths: list[str]
    suggestions: list[Suggestion]
    original_text: str = ""
    profile: ResumeProfile = ResumeProfile()


class AnalysisHistoryDTO(BaseModel):
    id: int
    overall_score: int | None = None
    content_score: int | None = None
    structure_score: int | None = None
    skill_match_score: int | None = None
    expression_score: int | None = None
    project_score: int | None = None
    summary: str | None = None
    analyzed_at: datetime
    strengths: list[str] = []
    suggestions: list[Suggestion] = []
    profile: ResumeProfile | None = None


class ResumeListItemDTO(BaseModel):
    id: int
    filename: str
    file_size: int | None = None
    uploaded_at: datetime
    access_count: int = 0
    latest_score: int | None = None
    last_analyzed_at: datetime | None = None
    interview_count: int = 0
    analyze_status: AsyncTaskStatus = AsyncTaskStatus.PENDING
    analyze_error: str | None = None


class ResumeDetailDTO(BaseModel):
    id: int
    filename: str
    file_size: int | None = None
    content_type: str | None = None
    storage_url: str | None = None
    uploaded_at: datetime
    access_count: int = 0
    resume_text: str | None = None
    analyze_status: AsyncTaskStatus = AsyncTaskStatus.PENDING
    analyze_error: str | None = None
    analyses: list[AnalysisHistoryDTO] = []

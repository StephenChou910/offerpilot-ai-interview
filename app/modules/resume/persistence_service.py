import json
import logging
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.base_persistence_service import BasePersistenceService, safe_json_loads
from app.common.error_code import ErrorCode
from app.common.model import AsyncTaskStatus
from app.modules.resume.models import (
    ResumeAnalysisEntity, ResumeEntity, ResumeVersionEntity, ResumeProfileEntity,
    ResumeProjectEntity, ResumeSkillEntity, ResumeWorkExperienceEntity, ResumeProjectSkillEntity,
)
from app.modules.resume.schemas import (
    AnalysisHistoryDTO,
    ResumeAnalysisResponse,
    ResumeDetailDTO,
    ResumeListItemDTO,
    ResumeProfile,
    StructuredResumeUpdate,
    Suggestion,
)

logger = logging.getLogger(__name__)


class ResumePersistenceService(BasePersistenceService[ResumeEntity]):
    model = ResumeEntity
    not_found_error = ErrorCode.RESUME_NOT_FOUND

    async def find_by_file_hash(
        self, db: AsyncSession, file_hash: str, user_id: int | None = None
    ) -> ResumeEntity | None:
        query = select(ResumeEntity).where(ResumeEntity.file_hash == file_hash)
        if user_id is not None:
            query = query.where(ResumeEntity.user_id == user_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def exists_by_file_hash(self, db: AsyncSession, file_hash: str, user_id: int | None = None) -> bool:
        query = select(ResumeEntity.id).where(ResumeEntity.file_hash == file_hash)
        if user_id is not None:
            query = query.where(ResumeEntity.user_id == user_id)
        result = await db.execute(query)
        return result.scalar_one_or_none() is not None

    async def find_all(self, db: AsyncSession, user_id: int | None = None) -> list[ResumeEntity]:
        query = select(ResumeEntity).order_by(ResumeEntity.uploaded_at.desc())
        if user_id is not None:
            query = query.where(ResumeEntity.user_id == user_id)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def save_resume(self, db: AsyncSession, entity: ResumeEntity) -> ResumeEntity:
        return await self.save(db, entity)

    async def clear_analyses(self, db: AsyncSession, resume_id: int) -> None:
        await db.execute(delete(ResumeAnalysisEntity).where(ResumeAnalysisEntity.resume_id == resume_id))
        await db.flush()

    async def delete_resume(self, db: AsyncSession, resume_id: int) -> None:
        await self.clear_analyses(db, resume_id)
        await db.execute(delete(ResumeEntity).where(ResumeEntity.id == resume_id))
        await db.flush()

    async def update_analyze_status(
        self, db: AsyncSession, resume_id: int, status: AsyncTaskStatus, error: str | None = None
    ) -> None:
        entity = await self.find_by_id(db, resume_id)
        if entity:
            entity.analyze_status = status
            entity.analyze_error = error[:500] if error and len(error) > 500 else error
            await db.flush()

    async def save_analysis(
        self, db: AsyncSession, resume_id: int, analysis: ResumeAnalysisResponse
    ) -> ResumeAnalysisEntity:
        entity = ResumeAnalysisEntity(
            resume_id=resume_id,
            overall_score=analysis.overall_score,
            content_score=analysis.score_detail.content_score,
            structure_score=analysis.score_detail.structure_score,
            skill_match_score=analysis.score_detail.skill_match_score,
            expression_score=analysis.score_detail.expression_score,
            project_score=analysis.score_detail.project_score,
            summary=analysis.summary,
            strengths_json=json.dumps(analysis.strengths, ensure_ascii=False),
            suggestions_json=json.dumps([s.model_dump() for s in analysis.suggestions], ensure_ascii=False),
            profile_json=json.dumps(analysis.profile.model_dump(), ensure_ascii=False) if analysis.profile else None,
            analyzed_at=datetime.now(),
        )
        db.add(entity)
        await db.flush()
        return entity

    async def save_structured_profile(
        self, db: AsyncSession, resume: ResumeEntity, analysis: ResumeAnalysisResponse
    ) -> ResumeVersionEntity:
        """Persist the normalized projection while retaining the raw profile_json."""
        latest = await db.scalar(
            select(ResumeVersionEntity.version_no)
            .where(ResumeVersionEntity.resume_id == resume.id)
            .order_by(ResumeVersionEntity.version_no.desc())
            .limit(1)
        )
        version = ResumeVersionEntity(
            resume_id=resume.id,
            version_no=(latest or 0) + 1,
            source_text=resume.resume_text,
            extraction_method=resume.extraction_method,
            confidence=(safe_json_loads(resume.text_quality_json, {}).get("ocr_confidence")
                        or (1.0 if resume.extraction_method == "text" else 0.8)),
            source_page=1, source_locator="resume_text",
            source_type=resume.extraction_method,
            source_excerpt=(resume.resume_text or "")[:500],
        )
        profile = ResumeProfileEntity(
            summary=analysis.profile.summary if analysis.profile else analysis.summary,
            experience_level=analysis.profile.experience_level if analysis.profile else "unknown",
        )
        version.profile = profile
        skill_entities = {}
        for skill in (analysis.profile.tech_stacks if analysis.profile else []):
            entity = ResumeSkillEntity(name=skill.name, proficiency=skill.proficiency,
                                       context=skill.context, source_text=skill.context, confidence=0.8,
                                       source_page=1, source_locator=f"profile.tech_stacks[{len(skill_entities)}]")
            entity.source_type = resume.extraction_method
            entity.source_excerpt = skill.context[:500]
            profile.skills.append(entity)
            skill_entities[skill.name.strip().lower()] = entity
        for index, project in enumerate(analysis.profile.projects if analysis.profile else []):
            project_entity = ResumeProjectEntity(
                sort_order=index, name=project.name, role=project.role,
                description=project.description,
                highlights=json.dumps(project.highlights, ensure_ascii=False),
                source_text=project.description, confidence=0.8,
                source_page=1, source_locator=f"profile.projects[{index}]",
                source_type=resume.extraction_method, source_excerpt=project.description[:500],
            )
            profile.projects.append(project_entity)
            for skill_name in project.tech_stack:
                skill_entity = skill_entities.get(skill_name.strip().lower())
                if skill_entity:
                    project_entity.skills.append(skill_entity)
        for index, experience in enumerate(analysis.profile.work_experiences if analysis.profile else []):
            profile.work_experiences.append(ResumeWorkExperienceEntity(
                sort_order=index, company=experience.company, title=experience.title,
                start_date=experience.start_date, end_date=experience.end_date,
                description=experience.description, source_text=experience.description,
                confidence=0.8, source_page=1, source_locator=f"profile.work_experiences[{index}]",
                source_type=resume.extraction_method, source_excerpt=experience.description[:500],
            ))
        db.add(version)
        await db.flush()
        return version

    async def find_analyses_by_resume_id(self, db: AsyncSession, resume_id: int) -> list[ResumeAnalysisEntity]:
        result = await db.execute(
            select(ResumeAnalysisEntity)
            .where(ResumeAnalysisEntity.resume_id == resume_id)
            .order_by(ResumeAnalysisEntity.analyzed_at.desc())
        )
        return list(result.scalars().all())

    async def find_latest_analysis(self, db: AsyncSession, resume_id: int) -> ResumeAnalysisEntity | None:
        result = await db.execute(
            select(ResumeAnalysisEntity)
            .where(ResumeAnalysisEntity.resume_id == resume_id)
            .order_by(ResumeAnalysisEntity.analyzed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_structured_profile(self, db: AsyncSession, resume_id: int, user_id: int) -> dict | None:
        resume = await db.scalar(select(ResumeEntity).where(ResumeEntity.id == resume_id, ResumeEntity.user_id == user_id))
        if resume is None:
            return None
        version = await db.scalar(
            select(ResumeVersionEntity)
            .where(ResumeVersionEntity.resume_id == resume_id)
            .options(selectinload(ResumeVersionEntity.profile).selectinload(ResumeProfileEntity.projects),
                     selectinload(ResumeVersionEntity.profile).selectinload(ResumeProfileEntity.skills),
                     selectinload(ResumeVersionEntity.profile).selectinload(ResumeProfileEntity.work_experiences))
            .order_by(ResumeVersionEntity.version_no.desc()).limit(1)
        )
        if version is None or version.profile is None:
            return None
        profile = version.profile
        return {
            "resume_id": resume_id, "version": version.version_no,
            "extraction_method": version.extraction_method, "confidence": version.confidence,
            "source_page": version.source_page, "source_locator": version.source_locator,
            "source_type": version.source_type, "source_excerpt": version.source_excerpt,
            "summary": profile.summary, "experience_level": profile.experience_level,
            "projects": [{"name": p.name, "role": p.role, "description": p.description,
                          "highlights": safe_json_loads(p.highlights, []),
                          "tech_stack": [skill.name for skill in p.skills],
                          "source_page": p.source_page, "source_locator": p.source_locator,
                          "source_type": p.source_type, "source_excerpt": p.source_excerpt} for p in profile.projects],
            "skills": [{"name": s.name, "proficiency": s.proficiency, "context": s.context,
                        "source_page": s.source_page, "source_locator": s.source_locator,
                        "source_type": s.source_type, "source_excerpt": s.source_excerpt} for s in profile.skills],
            "work_experiences": [{"company": w.company, "title": w.title, "start_date": w.start_date,
                                  "end_date": w.end_date, "description": w.description,
                                  "source_page": w.source_page, "source_locator": w.source_locator,
                                  "source_type": w.source_type, "source_excerpt": w.source_excerpt}
                                 for w in profile.work_experiences],
        }

    async def update_structured_profile(
        self, db: AsyncSession, resume_id: int, user_id: int, update: StructuredResumeUpdate
    ) -> dict | None:
        """Create a new manually corrected version without mutating history."""
        resume = await db.scalar(select(ResumeEntity).where(ResumeEntity.id == resume_id, ResumeEntity.user_id == user_id))
        if resume is None:
            return None
        latest = await db.scalar(
            select(ResumeVersionEntity.version_no)
            .where(ResumeVersionEntity.resume_id == resume_id)
            .order_by(ResumeVersionEntity.version_no.desc()).limit(1)
        )
        version = ResumeVersionEntity(
            resume_id=resume_id, version_no=(latest or 0) + 1,
            source_text=resume.resume_text, extraction_method="manual", confidence=1.0,
        )
        profile = ResumeProfileEntity(summary=update.summary, experience_level=update.experience_level)
        version.profile = profile
        skill_entities = {}
        for skill in update.skills:
            entity = ResumeSkillEntity(name=skill.name, proficiency=skill.proficiency, context=skill.context,
                                       source_text=skill.context, confidence=1.0,
                                       source_page=1, source_locator=f"manual.skills[{len(skill_entities)}]")
            entity.source_type = "manual"
            entity.source_excerpt = skill.context[:500]
            profile.skills.append(entity)
            skill_entities[skill.name.strip().lower()] = entity
        for index, project in enumerate(update.projects):
            project_entity = ResumeProjectEntity(
                sort_order=index, name=project.name, role=project.role,
                description=project.description,
                highlights=json.dumps(project.highlights, ensure_ascii=False),
                source_text=project.description, confidence=1.0,
                source_page=1, source_locator=f"manual.projects[{index}]",
                source_type="manual", source_excerpt=project.description[:500],
            )
            profile.projects.append(project_entity)
            project_entity.skills.extend(skill_entities.get(name.strip().lower()) for name in project.tech_stack
                                         if skill_entities.get(name.strip().lower()))
        for index, experience in enumerate(update.work_experiences):
            profile.work_experiences.append(ResumeWorkExperienceEntity(
                sort_order=index, company=experience.company, title=experience.title,
                start_date=experience.start_date, end_date=experience.end_date,
                description=experience.description, source_text=experience.description,
                confidence=1.0, source_page=1, source_locator=f"manual.work_experiences[{index}]",
                source_type="manual", source_excerpt=experience.description[:500],
            ))
        db.add(version)
        await db.flush()
        return await self.get_structured_profile(db, resume_id, user_id)

    def to_list_item_dto(self, entity: ResumeEntity) -> ResumeListItemDTO:
        latest = entity.analyses[0] if entity.analyses else None
        return ResumeListItemDTO(
            id=entity.id,
            filename=entity.original_filename,
            file_size=entity.file_size,
            uploaded_at=entity.uploaded_at,
            access_count=entity.access_count,
            latest_score=latest.overall_score if latest else None,
            last_analyzed_at=latest.analyzed_at if latest else None,
            interview_count=0,
            analyze_status=entity.analyze_status,
            analyze_error=entity.analyze_error,
        )

    def to_detail_dto(self, entity: ResumeEntity) -> ResumeDetailDTO:
        analyses = [self._to_analysis_history_dto(a) for a in entity.analyses]
        return ResumeDetailDTO(
            id=entity.id,
            filename=entity.original_filename,
            file_size=entity.file_size,
            content_type=entity.content_type,
            storage_url=entity.storage_url,
            uploaded_at=entity.uploaded_at,
            access_count=entity.access_count,
            resume_text=entity.resume_text,
            analyze_status=entity.analyze_status,
            analyze_error=entity.analyze_error,
            analyses=analyses,
        )

    @staticmethod
    def _to_analysis_history_dto(entity: ResumeAnalysisEntity) -> AnalysisHistoryDTO:
        strengths = safe_json_loads(entity.strengths_json, [])
        raw_suggestions = safe_json_loads(entity.suggestions_json, [])
        suggestions = [Suggestion(**s) for s in raw_suggestions] if raw_suggestions else []
        raw_profile = safe_json_loads(entity.profile_json, None)
        profile = ResumeProfile(**raw_profile) if raw_profile else None

        return AnalysisHistoryDTO(
            id=entity.id,
            overall_score=entity.overall_score,
            content_score=entity.content_score,
            structure_score=entity.structure_score,
            skill_match_score=entity.skill_match_score,
            expression_score=entity.expression_score,
            project_score=entity.project_score,
            summary=entity.summary,
            analyzed_at=entity.analyzed_at,
            strengths=strengths,
            suggestions=suggestions,
            profile=profile,
        )


resume_persistence_service = ResumePersistenceService()

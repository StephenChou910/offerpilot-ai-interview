"""Generate a read-only quality report for structured resume migration."""
import asyncio
import json
from sqlalchemy import func, select
from app import database
from app.modules.resume.models import (
    ResumeEntity, ResumeAnalysisEntity, ResumeVersionEntity, ResumeProjectEntity,
    ResumeSkillEntity, ResumeProjectSkillEntity,
)


async def main() -> None:
    database.init_engine()
    async with database.async_session_factory() as db:
        total = await db.scalar(select(func.count(ResumeEntity.id))) or 0
        analyzed = await db.scalar(select(func.count(ResumeAnalysisEntity.id)).where(ResumeAnalysisEntity.profile_json.is_not(None))) or 0
        structured = await db.scalar(select(func.count(func.distinct(ResumeVersionEntity.resume_id)))) or 0
        failed = await db.scalar(select(func.count(ResumeEntity.id)).where(ResumeEntity.analyze_status == "FAILED")) or 0
        ocr = await db.scalar(select(func.count(ResumeEntity.id)).where(ResumeEntity.extraction_method != "text")) or 0
        projects = await db.scalar(select(func.count(ResumeProjectEntity.id))) or 0
        skills = await db.scalar(select(func.count(ResumeSkillEntity.id))) or 0
        relations = await db.scalar(select(func.count(ResumeProjectSkillEntity.id))) or 0
    await database.close_db()
    report = {
        "resume_total": total, "analyzed_with_profile": analyzed,
        "structured_resume_count": structured, "analyze_failed": failed,
        "ocr_or_manual_count": ocr, "projects": projects, "skills": skills,
        "project_skill_relations": relations,
        "structured_coverage": round(structured / analyzed, 4) if analyzed else 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

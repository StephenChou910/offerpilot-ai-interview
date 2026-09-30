"""Read-only field-level completeness sample against raw profile_json."""
import asyncio, json
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app import database
from app.modules.resume.models import ResumeAnalysisEntity, ResumeVersionEntity


async def main() -> None:
    database.init_engine()
    rows = projects_ok = skills_ok = experiences_ok = 0
    async with database.async_session_factory() as db:
        analyses = (await db.execute(select(ResumeAnalysisEntity).where(ResumeAnalysisEntity.profile_json.is_not(None)))).scalars().all()
        for analysis in analyses:
            raw = json.loads(analysis.profile_json or "{}")
            version = await db.scalar(select(ResumeVersionEntity).where(
                ResumeVersionEntity.resume_id == analysis.resume_id
            ).options(selectinload(ResumeVersionEntity.profile)).order_by(ResumeVersionEntity.version_no.desc()).limit(1))
            if not version or not version.profile:
                continue
            rows += 1
            profile = version.profile
            if len(profile.projects) >= len(raw.get("projects", [])): projects_ok += 1
            if len(profile.skills) >= len(raw.get("techStacks", raw.get("tech_stacks", []))): skills_ok += 1
            if len(profile.work_experiences) >= len(raw.get("workExperiences", raw.get("work_experiences", []))): experiences_ok += 1
    await database.close_db()
    result = {"sample_size": rows, "projects_complete": projects_ok, "skills_complete": skills_ok,
              "work_experiences_complete": experiences_ok,
              "projects_rate": round(projects_ok / rows, 4) if rows else 0,
              "skills_rate": round(skills_ok / rows, 4) if rows else 0,
              "work_experiences_rate": round(experiences_ok / rows, 4) if rows else 0}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

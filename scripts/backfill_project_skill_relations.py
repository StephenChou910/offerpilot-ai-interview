"""Backfill project-skill relations from the original profile_json."""
import asyncio
import json
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app import database
from app.modules.resume.models import ResumeAnalysisEntity, ResumeProjectEntity, ResumeSkillEntity, ResumeProjectSkillEntity


async def main() -> None:
    database.init_engine()
    created = 0
    async with database.async_session_factory() as db:
        analyses = (await db.execute(select(ResumeAnalysisEntity).where(ResumeAnalysisEntity.profile_json.is_not(None)))).scalars().all()
        for analysis in analyses:
            raw = json.loads(analysis.profile_json or "{}")
            projects = raw.get("projects", [])
            # Match the projects belonging to this analysis through its resume's latest profile.
            from app.modules.resume.models import ResumeVersionEntity, ResumeProfileEntity
            version = await db.scalar(select(ResumeVersionEntity).where(
                ResumeVersionEntity.resume_id == analysis.resume_id
            ).options(selectinload(ResumeVersionEntity.profile)).order_by(ResumeVersionEntity.version_no.desc()).limit(1))
            if not version or not version.profile:
                continue
            profile = version.profile
            project_entities = (await db.execute(select(ResumeProjectEntity).where(
                ResumeProjectEntity.profile_id == profile.id
            ).order_by(ResumeProjectEntity.sort_order))).scalars().all()
            skills = (await db.execute(select(ResumeSkillEntity).where(
                ResumeSkillEntity.profile_id == profile.id
            ))).scalars().all()
            skill_map = {skill.name.strip().lower(): skill for skill in skills}
            for index, project in enumerate(projects):
                if index >= len(project_entities):
                    break
                project_entity = project_entities[index]
                for name in project.get("techStack", project.get("tech_stack", [])):
                    skill = skill_map.get(str(name).strip().lower())
                    if not skill:
                        continue
                    exists = await db.scalar(select(ResumeProjectSkillEntity.id).where(
                        ResumeProjectSkillEntity.project_id == project_entity.id,
                        ResumeProjectSkillEntity.skill_id == skill.id,
                    ))
                    if not exists:
                        db.add(ResumeProjectSkillEntity(project_id=project_entity.id, skill_id=skill.id))
                        created += 1
        await db.commit()
    await database.close_db()
    print(json.dumps({"created": created}, ensure_ascii=False))


if __name__ == "__main__":
    import json
    asyncio.run(main())

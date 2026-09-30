"""Backfill normalized resume tables from existing resume_analyses.profile_json.

Idempotent: resumes that already have a structured version are skipped. The raw
analysis JSON is never modified.
"""

import argparse
import asyncio
import json
import logging

from sqlalchemy import select

from app import database
from app.modules.resume.models import ResumeAnalysisEntity, ResumeEntity, ResumeVersionEntity
from app.modules.resume.persistence_service import resume_persistence_service
from app.modules.resume.schemas import ResumeAnalysisResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def backfill(limit: int | None = None) -> dict[str, int]:
    database.init_engine()
    stats = {"scanned": 0, "backfilled": 0, "skipped": 0, "failed": 0}
    async with database.async_session_factory() as db:
        query = (
            select(ResumeEntity, ResumeAnalysisEntity)
            .join(ResumeAnalysisEntity, ResumeAnalysisEntity.resume_id == ResumeEntity.id)
            .where(ResumeAnalysisEntity.profile_json.is_not(None))
            .order_by(ResumeAnalysisEntity.analyzed_at.asc())
        )
        if limit:
            query = query.limit(limit)
        rows = (await db.execute(query)).all()
        for resume, analysis in rows:
            stats["scanned"] += 1
            exists = await db.scalar(
                select(ResumeVersionEntity.id)
                .where(ResumeVersionEntity.resume_id == resume.id)
                .limit(1)
            )
            if exists:
                stats["skipped"] += 1
                continue
            try:
                raw = json.loads(analysis.profile_json or "{}")
                payload = {
                    "overall_score": analysis.overall_score or 0,
                    "score_detail": {
                        "content_score": analysis.content_score or 0,
                        "structure_score": analysis.structure_score or 0,
                        "skill_match_score": analysis.skill_match_score or 0,
                        "expression_score": analysis.expression_score or 0,
                        "project_score": analysis.project_score or 0,
                    },
                    "summary": analysis.summary or "",
                    "strengths": json.loads(analysis.strengths_json or "[]"),
                    "suggestions": json.loads(analysis.suggestions_json or "[]"),
                    "profile": raw,
                }
                parsed = ResumeAnalysisResponse.model_validate(payload)
                await resume_persistence_service.save_structured_profile(db, resume, parsed)
                await db.commit()
                stats["backfilled"] += 1
            except Exception as exc:
                await db.rollback()
                stats["failed"] += 1
                logger.exception("resume_id=%s 回填失败: %s", resume.id, exc)
    await database.close_db()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="回填简历结构化数据")
    parser.add_argument("--limit", type=int, default=None, help="最多处理条数")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(backfill(args.limit)), ensure_ascii=False))


if __name__ == "__main__":
    main()

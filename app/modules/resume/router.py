import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.result import Result
from app.database import get_db
from app.infrastructure.export.pdf_export_service import pdf_export_service
from app.infrastructure.redis.redis_service import RedisService, get_redis
from app.modules.auth.dependencies import get_current_user_id
from app.modules.resume.async_tasks import AnalyzeStreamProducer
from app.modules.resume.delete_service import resume_delete_service
from app.modules.resume.history_service import resume_history_service
from app.modules.resume.schemas import ResumeDetailDTO, ResumeListItemDTO
from app.modules.resume.upload_service import resume_upload_service
from app.modules.resume.persistence_service import resume_persistence_service
from app.modules.resume.schemas import StructuredResumeUpdate
from app.common.request_context import get_request_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _enqueue_analysis(resume_id: int, user_id: int | None = None) -> None:
    """在事务提交后异步触发简历分析。"""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_enqueue(resume_id, user_id, get_request_id()))
    except RuntimeError:
        pass


async def _do_enqueue(resume_id: int, user_id: int | None = None, request_id: str | None = None) -> None:
    redis = await get_redis()
    producer = AnalyzeStreamProducer(RedisService(redis))
    await producer.send_analyze_task(resume_id, user_id, request_id)


@router.get("", response_model=Result[list[ResumeListItemDTO]])
async def list_resumes(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    items = await resume_history_service.get_resume_list(db, user_id)
    return Result.success(items)


@router.get("/{resume_id}", response_model=Result[ResumeDetailDTO])
async def get_resume(
    resume_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    detail = await resume_history_service.get_resume_detail(db, resume_id, user_id)
    return Result.success(detail)


@router.get("/{resume_id}/structured", response_model=Result[dict])
async def get_structured_resume(
    resume_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    structured = await resume_persistence_service.get_structured_profile(db, resume_id, user_id)
    if structured is None:
        raise HTTPException(status_code=404, detail="结构化简历不存在")
    return Result.success(structured)


@router.put("/{resume_id}/structured", response_model=Result[dict])
async def update_structured_resume(
    resume_id: int,
    payload: StructuredResumeUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    structured = await resume_persistence_service.update_structured_profile(db, resume_id, user_id, payload)
    if structured is None:
        raise HTTPException(status_code=404, detail="结构化简历不存在")
    await db.commit()
    return Result.success(structured)


@router.post("", response_model=Result[ResumeDetailDTO])
async def upload_resume(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    file_bytes = await file.read()
    entity = await resume_upload_service.upload(db, file_bytes, file.filename or "unknown", file.content_type, user_id)

    await db.commit()

    if entity.resume_text and entity.analyze_status != "FAILED":
        _enqueue_analysis(entity.id, user_id)

    detail = await resume_history_service.get_resume_detail(db, entity.id, user_id)
    return Result.success(detail)


@router.delete("/{resume_id}", response_model=Result[None])
async def delete_resume(
    resume_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await resume_delete_service.delete_resume(db, resume_id, user_id)
    return Result.success(None)


@router.post("/{resume_id}/reanalyze", response_model=Result[None])
async def reanalyze_resume(
    resume_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await resume_upload_service.reanalyze(db, resume_id, user_id)

    await db.commit()

    _enqueue_analysis(resume_id, user_id)

    return Result.success(None)


@router.get("/{resume_id}/export-pdf")
async def export_resume_pdf(
    resume_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    detail = await resume_history_service.get_resume_detail(db, resume_id, user_id)
    pdf_bytes = await pdf_export_service.export_resume_analysis_pdf(detail)
    filename = f"resume-analysis-{resume_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

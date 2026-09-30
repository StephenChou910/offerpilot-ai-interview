import logging
import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.error_code import ErrorCode
from app.common.exception import BusinessException
from app.common.model import AsyncTaskStatus
from app.config import settings
from app.infrastructure.file.document_parse_service import document_parse_service
from app.infrastructure.file.file_hash_service import file_hash_service
from app.infrastructure.file.file_storage_service import file_storage_service
from app.infrastructure.file.file_validation_service import file_validation_service
from app.infrastructure.redis.redis_service import RedisService, get_redis
from app.infrastructure.file.ocr_service import build_ocr_service
from app.modules.resume.async_tasks import AnalyzeStreamProducer
from app.modules.resume.models import ResumeEntity
from app.modules.resume.persistence_service import resume_persistence_service
from app.modules.resume.text_quality_service import resume_text_quality_service

logger = logging.getLogger(__name__)


class ResumeUploadService:
    async def upload(
        self, db: AsyncSession, file_bytes: bytes, filename: str, content_type: str | None, user_id: int = 0
    ) -> ResumeEntity:
        safe_filename = file_validation_service.validate_file(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            max_size=settings.resume.max_file_size,
            allowed_types=settings.resume.allowed_types,
            file_type_name="简历",
        )

        file_hash = file_hash_service.calculate_hash(file_bytes)

        existing = await resume_persistence_service.find_by_file_hash(db, file_hash, user_id=user_id)
        if existing:
            existing.increment_access_count()
            await db.flush()
            logger.info("当前用户简历已存在(哈希去重): user_id=%d, id=%d", user_id, existing.id)
            return existing

        storage_key, storage_url = await file_storage_service.upload_resume(file_bytes, safe_filename, content_type)
        resume_text = await document_parse_service.parse_content(file_bytes, filename)
        text_quality = resume_text_quality_service.assess(resume_text)
        extraction_method = "text"
        if text_quality["needs_ocr"]:
            ocr_result = await build_ocr_service().extract_text(file_bytes, filename)
            ocr_quality = resume_text_quality_service.assess(ocr_result.text)
            if ocr_result.text and not ocr_quality["needs_ocr"]:
                resume_text = ocr_result.text
                text_quality = {**ocr_quality, "ocr_provider": ocr_result.provider,
                                "ocr_confidence": ocr_result.confidence, "ocr_pages": ocr_result.pages}
                extraction_method = ocr_result.provider
            else:
                text_quality = {**text_quality, "ocr_provider": ocr_result.provider,
                                "ocr_error": ocr_result.error, "ocr_confidence": ocr_result.confidence}

        entity = ResumeEntity(
            user_id=user_id,
            file_hash=file_hash,
            original_filename=safe_filename,
            file_size=len(file_bytes),
            content_type=content_type,
            storage_key=storage_key,
            storage_url=storage_url,
            resume_text=resume_text,
            analyze_status=AsyncTaskStatus.PENDING,
            text_quality_json=json.dumps(text_quality, ensure_ascii=False),
            extraction_method=extraction_method,
        )

        entity = await resume_persistence_service.save_resume(db, entity)
        await db.commit()

        if not resume_text:
            await resume_persistence_service.update_analyze_status(
                db, entity.id, AsyncTaskStatus.FAILED, "简历解析结果为空"
            )

        return entity

    async def reanalyze(self, db: AsyncSession, resume_id: int, user_id: int = 0) -> None:
        entity = await resume_persistence_service.find_by_id_or_throw(db, resume_id, user_id)

        if not entity.resume_text:
            raise BusinessException(ErrorCode.RESUME_PARSE_FAILED, "简历文本为空，无法重新分析")

        await resume_persistence_service.update_analyze_status(db, resume_id, AsyncTaskStatus.PENDING, None)
        await db.commit()
        await self._enqueue_analysis(resume_id)

    @staticmethod
    async def _enqueue_analysis(resume_id: int) -> None:
        redis = await get_redis()
        producer = AnalyzeStreamProducer(RedisService(redis))
        import uuid
        await producer.send_analyze_task(resume_id, request_id=f"reanalyze-{uuid.uuid4().hex}", force=True)


resume_upload_service = ResumeUploadService()

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import app.database as db_module
from app.common.config_check import build_config_check_report, log_config_check_report
from app.common.request_context import request_id_context
from app.common.log_redaction import RedactionFilter
from app.common.exception_handlers import register_exception_handlers
from app.config import settings
from app.database import close_db, init_db, init_engine
from app.infrastructure.redis.redis_service import RedisService, close_redis, init_redis
from app.infrastructure.redis.stream_worker import StreamWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactionFilter())
    worker_tasks: list[asyncio.Task] = []
    workers: list[StreamWorker] = []

    config_report = build_config_check_report(settings)
    app.state.config_report = config_report
    log_config_check_report(config_report, logger)
    if settings.strict_config and config_report.has_errors:
        raise RuntimeError("配置检查失败，请修复 ERROR 项后再启动服务")

    logger.info("正在初始化数据库引擎...")
    try:
        init_engine()
        logger.info("数据库引擎初始化成功")
    except Exception as e:
        logger.error("数据库引擎初始化失败: %s", e)
        raise

    # 等待 DNS 就绪
    logger.info("等待 DNS 解析就绪...")
    await asyncio.sleep(5)

    logger.info("正在初始化数据库...")
    try:
        await init_db()
        logger.info("数据库初始化成功")
    except Exception as e:
        logger.warning("数据库初始化失败（服务仍可启动）: %s", e)

    logger.info("正在连接 Redis...")
    try:
        redis = await init_redis()
        app.state.redis = redis
        logger.info("Redis 连接成功，redis对象: %s", redis)

        if redis is not None and db_module.async_session_factory is not None:
            from app.modules.interview.async_tasks import (
                EVALUATE_STREAM_KEY,
                InterviewEvaluateTaskHandler,
            )
            from app.modules.knowledge_base.async_tasks import (
                KNOWLEDGE_BASE_INDEX_STREAM_KEY,
                KnowledgeBaseIndexTaskHandler,
            )
            from app.modules.resume.async_tasks import (
                RESUME_ANALYZE_STREAM_KEY,
                ResumeAnalyzeTaskHandler,
            )

            redis_service = RedisService(redis)

            resume_handler = ResumeAnalyzeTaskHandler(
                db_module.async_session_factory, stream_key=RESUME_ANALYZE_STREAM_KEY
            )
            interview_handler = InterviewEvaluateTaskHandler(
                db_module.async_session_factory, stream_key=EVALUATE_STREAM_KEY
            )
            knowledge_base_handler = KnowledgeBaseIndexTaskHandler(
                db_module.async_session_factory, stream_key=KNOWLEDGE_BASE_INDEX_STREAM_KEY
            )

            resume_worker = StreamWorker(
                name="resume-analyze-worker",
                redis_service=redis_service,
                stream_key=RESUME_ANALYZE_STREAM_KEY,
                handler=resume_handler.handle,
                cancel_handler=resume_handler.cancel,
                dead_letter_handler=resume_handler.dead_letter,
            )
            interview_worker = StreamWorker(
                name="interview-evaluate-worker",
                redis_service=redis_service,
                stream_key=EVALUATE_STREAM_KEY,
                handler=interview_handler.handle,
                cancel_handler=interview_handler.cancel,
                dead_letter_handler=interview_handler.dead_letter,
            )
            knowledge_base_worker = StreamWorker(
                name="knowledge-base-index-worker",
                redis_service=redis_service,
                stream_key=KNOWLEDGE_BASE_INDEX_STREAM_KEY,
                handler=knowledge_base_handler.handle,
                cancel_handler=knowledge_base_handler.cancel,
                dead_letter_handler=knowledge_base_handler.dead_letter,
            )

            workers.extend([resume_worker, interview_worker, knowledge_base_worker])
            worker_tasks.extend(
                [
                    asyncio.create_task(resume_worker.run_forever(), name="resume-analyze-worker"),
                    asyncio.create_task(interview_worker.run_forever(), name="interview-evaluate-worker"),
                    asyncio.create_task(knowledge_base_worker.run_forever(), name="knowledge-base-index-worker"),
                ]
            )
            logger.info("异步任务 worker 已启动: %d 个", len(worker_tasks))
        else:
            logger.warning("Redis 或数据库会话工厂不可用，跳过异步 worker 启动")
    except Exception as e:
        logger.warning("Redis 连接失败（服务仍可启动）: %s", e)
        app.state.redis = None

    logger.info("应用启动完成")
    yield

    for worker in workers:
        worker.stop()
    for task in worker_tasks:
        task.cancel()
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    try:
        await close_redis()
    except Exception:
        pass

    try:
        await close_db()
    except Exception:
        pass


# 公开路径前缀（不需要认证）
PUBLIC_PATHS = (
    "/api/auth/login",
    "/api/auth/register",
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


async def auth_middleware(request: Request, call_next):
    """全局认证中间件"""
    path = request.url.path
    incoming_request_id = request.headers.get("X-Request-ID")
    request_id = incoming_request_id.strip()[:128] if incoming_request_id else uuid.uuid4().hex
    request.state.request_id = request_id
    context_token = request_id_context.set(request_id)

    # 公开路径不需要认证
    if any(path.startswith(p) for p in PUBLIC_PATHS):
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        request_id_context.reset(context_token)
        return response

    # OPTIONS 请求不需要认证（CORS 预检）
    if request.method == "OPTIONS":
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        request_id_context.reset(context_token)
        return response

    # 检查 Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        response = JSONResponse(
            status_code=401,
            content={"detail": "未提供认证凭证", "request_id": request_id},
        )
        response.headers["X-Request-ID"] = request_id
        request_id_context.reset(context_token)
        return response

    # 验证 token
    access_token = auth_header.split(" ", 1)[1]
    from app.modules.auth.security import decode_access_token

    payload = decode_access_token(access_token)
    if payload is None:
        response = JSONResponse(
            status_code=401,
            content={"detail": "无效的认证凭证", "request_id": request_id},
        )
        response.headers["X-Request-ID"] = request_id
        request_id_context.reset(context_token)
        return response

    # 将用户信息存入 request.state
    request.state.user_id = payload.get("sub")
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_context.reset(context_token)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # 全局认证中间件
    app.middleware("http")(auth_middleware)

    register_exception_handlers(app)

    _register_routers(app)

    @app.get("/api/health")
    async def health_check():
        return {"status": "UP", "service": settings.app_name}

    @app.get("/api/health/config")
    async def config_health_check():
        report = getattr(app.state, "config_report", None)
        if report is None:
            report = build_config_check_report(settings)
        return report.model_dump()

    @app.get("/api/health/capabilities")
    async def capabilities_health_check():
        """Expose feature readiness without exposing secrets or provider details."""
        return {
            "capabilities": {
                "resume_ocr": {"status": "PLANNED", "message": "当前版本使用文本抽取，OCR 兜底待阶段 2 实施"},
                "knowledge_graph": {"status": "EXPERIMENTAL", "message": "实体和三元组抽取可用，面试链路接入待阶段 3"},
                "agent_orchestration": {"status": "EXPERIMENTAL", "message": "主要用于知识任务，统一工作流待阶段 1 实施"},
                "github_tools": {"status": "EXPERIMENTAL", "message": "依赖外部凭据和服务限额"},
                "arxiv_tools": {"status": "PLACEHOLDER", "message": "当前为占位能力，不作为生产结果"},
            }
        }

    return app


def _register_routers(app: FastAPI) -> None:
    from app.modules.agent_orchestration.router import router as agent_router
    from app.modules.tasks.router import router as tasks_router
    from app.modules.agent_orchestration.smart_download_router import router as smart_download_router
    from app.modules.auth.router import router as auth_router
    from app.modules.demo.router import router as demo_router
    from app.modules.interview.router import router as interview_router
    from app.modules.interview.skill_router import router as skill_router
    from app.modules.knowledge_base.cross_kb_router import router as cross_kb_router
    from app.modules.knowledge_base.rag_router import router as rag_router
    from app.modules.knowledge_base.router import router as kb_router
    from app.modules.knowledge_graph.router import router as kg_router
    from app.modules.organization.router import router as organization_router
    from app.modules.resume.router import router as resume_router
    from app.modules.training.router import router as training_router

    app.include_router(auth_router, prefix="/api/auth", tags=["用户认证"])
    app.include_router(resume_router, prefix="/api/resumes", tags=["简历管理"])
    app.include_router(organization_router, prefix="/api/organizations", tags=["组织与学员管理"])
    app.include_router(demo_router, prefix="/api/demo", tags=["演示模式"])
    app.include_router(training_router, prefix="/api/training", tags=["个人训练计划"])
    app.include_router(interview_router, prefix="/api/interview", tags=["模拟面试"])
    app.include_router(skill_router, prefix="/api/interview/skills", tags=["面试方向"])
    app.include_router(kb_router, prefix="/api/knowledgebase", tags=["知识库管理"])
    app.include_router(rag_router, prefix="/api/knowledgebase", tags=["知识库问答"])
    app.include_router(cross_kb_router, prefix="/api/cross-knowledgebase", tags=["跨知识库问答"])
    app.include_router(kg_router, prefix="/api/knowledge-graph", tags=["知识图谱"])
    app.include_router(agent_router, tags=["智能Agent"])
    app.include_router(tasks_router)
    app.include_router(smart_download_router, tags=["智能下载"])


app = create_app()

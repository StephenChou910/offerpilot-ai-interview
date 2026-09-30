import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("AI_BAILIAN_API_KEY", "dummy-key")

from fastapi.testclient import TestClient

from app.common.config_check import build_config_check_report
from app.common.result import Result
from app.config import CorsSettings
from app.main import app

client = TestClient(app)


def test_health_endpoint_contract():
    response = client.get("/api/health")

    assert response.status_code == 200
    from app.config import settings

    assert response.json() == {"status": "UP", "service": settings.app_name}
    assert response.headers.get("X-Request-ID")


def test_capabilities_endpoint_is_public_and_explicit():
    response = client.get("/api/health/capabilities")

    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert capabilities["resume_ocr"]["status"] == "PLANNED"
    assert capabilities["arxiv_tools"]["status"] == "PLACEHOLDER"


def test_config_health_endpoint_contract():
    response = client.get("/api/health/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"OK", "WARN", "ERROR"}
    assert isinstance(payload["issues"], list)


def test_training_routes_are_registered():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/training/calibration" in paths
    assert "/api/training/plan" in paths
    assert "/api/training/tasks/progress" in paths
    assert "/api/training/trends" in paths


def test_dynamic_interview_routes_are_registered():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/interview/jd/parse" in paths
    assert "/api/interview/dynamic-sessions" in paths
    assert "/api/interview/dynamic-sessions/{session_id}/turns/{turn_id}/answer" in paths
    assert "/api/interview/dynamic-sessions/{session_id}/report" in paths
    assert "/api/interview/dynamic-sessions/{session_id}/topics/{topic_id}/rag-insight" in paths
    assert "/api/interview/dynamic-sessions/{session_id}/topics/{topic_id}/retry" in paths


def test_protected_endpoint_requires_bearer_token():
    response = client.get("/api/resumes")

    assert response.status_code == 401
    assert response.json()["detail"] == "未提供认证凭证"


def test_result_helpers_keep_response_shape():
    success = Result.success({"id": 1})
    failure = Result.error("bad request", code=400)

    assert success.model_dump() == {"code": 0, "message": "success", "data": {"id": 1}}
    assert failure.model_dump() == {"code": 400, "message": "bad request", "data": None}


def test_cors_origin_parser_trims_empty_values():
    settings = CorsSettings(allowed_origins="http://localhost:5173, http://localhost:5174,")

    assert settings.origins_list == ["http://localhost:5173", "http://localhost:5174"]


def test_config_report_flags_missing_core_services():
    from app.config import Settings

    settings = Settings(strict_config=True)
    settings.ai.bailian_api_key = "dummy-key"
    settings.database.host = "127.0.0.1"
    settings.database.port = 1
    settings.redis.host = "127.0.0.1"
    settings.redis.port = 1

    report = build_config_check_report(settings, check_ports=True)

    assert report.status == "ERROR"
    assert report.has_errors
    assert any(issue.key == "AI_BAILIAN_API_KEY" for issue in report.issues)


def test_strict_config_rejects_default_secrets(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("JWT_SECRET_KEY", "short")
    strict_settings = Settings(strict_config=True)
    strict_settings.database.password = "password"
    strict_settings.storage.secret_key = "minioadmin"

    report = build_config_check_report(strict_settings, check_ports=False)

    keys = {issue.key for issue in report.issues if issue.severity == "ERROR"}
    assert {"POSTGRES_PASSWORD", "APP_STORAGE_SECRET_KEY", "JWT_SECRET_KEY"}.issubset(keys)

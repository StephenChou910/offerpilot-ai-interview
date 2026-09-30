from app.common.log_redaction import redact_text
from app.config import Settings


def test_sensitive_log_redaction():
    text = "Authorization: Bearer abc123 token=secret phone 13800138000 mail a@b.com"
    result = redact_text(text)
    assert "abc123" not in result
    assert "secret" not in result
    assert "13800138000" not in result
    assert "a@b.com" not in result


def test_environment_is_explicitly_classified():
    settings = Settings(environment="staging")
    assert settings.environment == "staging"

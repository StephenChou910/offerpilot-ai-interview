import os
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("AI_BAILIAN_API_KEY", "dummy-key")

from app.common.ai.structured_output import StructuredOutputInvoker
from app.common.error_code import ErrorCode
from app.common.exception import BusinessException


class _ResultDTO(BaseModel):
    score: int
    summary: str


class _FakeModel:
    def __init__(self, contents: list[object]):
        self.contents = iter(contents)
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        return SimpleNamespace(content=next(self.contents))


@pytest.mark.asyncio
async def test_structured_output_extracts_json_after_reasoning_and_explanation():
    model = _FakeModel(['<think>internal reasoning</think>\n结果如下： {"score": 90, "summary": "结构完整"} 谢谢'])

    result = await StructuredOutputInvoker().invoke(
        chat_model=model,
        system_prompt="Return JSON",
        user_prompt="Analyze",
        output_model=_ResultDTO,
    )

    assert result == _ResultDTO(score=90, summary="结构完整")
    assert model.calls == 1


@pytest.mark.asyncio
async def test_structured_output_retries_once_after_schema_failure():
    model = _FakeModel(['{"score": "unknown", "summary": "first"}', '{"score": 88, "summary": "second"}'])

    result = await StructuredOutputInvoker().invoke(
        chat_model=model,
        system_prompt="Return JSON",
        user_prompt="Analyze",
        output_model=_ResultDTO,
    )

    assert result.score == 88
    assert model.calls == 2


@pytest.mark.asyncio
async def test_structured_output_returns_safe_error_after_all_schema_retries():
    model = _FakeModel(['not json', 'still not json'])

    with pytest.raises(BusinessException, match="模型响应未满足结构化格式"):
        await StructuredOutputInvoker().invoke(
            chat_model=model,
            system_prompt="Return JSON",
            user_prompt="Analyze",
            output_model=_ResultDTO,
            error_code=ErrorCode.RESUME_ANALYSIS_FAILED,
            error_prefix="简历分析失败：",
        )

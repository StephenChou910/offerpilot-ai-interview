import logging
import re
from typing import TypeVar

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.common.error_code import ErrorCode
from app.common.exception import BusinessException
from app.config import settings

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

STRICT_JSON_INSTRUCTION = """
请仅返回可被 JSON 解析器直接解析的 JSON 对象，并严格满足字段结构要求：
1) 不要输出 Markdown 代码块（如 ```json）。
2) 不要输出任何解释文字、前后缀、注释。
3) 所有字符串内引号必须正确转义。
"""


def _content_to_text(content: object) -> str:
    """Normalize provider-specific message content without logging user data."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
        return "\n".join(text_parts)
    return str(content or "")


def _extract_json_object(content: str) -> str:
    """Extract the first complete JSON object from a model response.

    Some OpenAI-compatible providers prepend a reasoning block or a short
    explanation even when the prompt requires JSON only.  Keeping this small
    normalization step before Pydantic validation makes those responses usable
    while the schema remains the final source of truth.
    """
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    start = text.find("{")
    if start < 0:
        return text

    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text


class StructuredOutputInvoker:
    def __init__(self):
        self.max_attempts = settings.ai.structured_max_attempts
        self.include_last_error = settings.ai.structured_include_last_error
        self.use_repair_prompt = settings.ai.structured_retry_use_repair_prompt

    async def invoke(
        self,
        chat_model: ChatOpenAI,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        error_code: ErrorCode = ErrorCode.AI_SERVICE_ERROR,
        error_prefix: str = "",
        log_context: str = "",
    ) -> T:
        parser = PydanticOutputParser(pydantic_object=output_model)
        format_instructions = parser.get_format_instructions()
        full_system = f"{system_prompt}\n\n{format_instructions}"

        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            attempt_system = full_system if attempt == 1 else self._build_retry_prompt(full_system, last_error)
            try:
                messages = [
                    ("system", attempt_system),
                    ("human", user_prompt),
                ]
                response = await chat_model.ainvoke(messages)
                content = _extract_json_object(_content_to_text(response.content))
                return parser.parse(content)
            except OutputParserException as e:
                last_error = e
                if attempt < self.max_attempts:
                    logger.warning(
                        "%s结构化解析失败，准备重试: attempt=%d/%d, error_type=%s",
                        log_context,
                        attempt,
                        self.max_attempts,
                        type(e).__name__,
                    )
                else:
                    logger.error(
                        "%s结构化解析失败，已达最大重试次数: attempts=%d, error_type=%s",
                        log_context,
                        self.max_attempts,
                        type(e).__name__,
                    )

            except Exception as e:
                logger.error("%s调用 AI 服务失败: error_type=%s", log_context, type(e).__name__)
                raise BusinessException(error_code, f"{error_prefix}AI 服务调用失败，请稍后重试") from e

        raise BusinessException(error_code, f"{error_prefix}模型响应未满足结构化格式，请重试")

    def _build_retry_prompt(self, original_system: str, last_error: Exception | None) -> str:
        if not self.use_repair_prompt:
            return original_system

        parts = [original_system, "\n\n", STRICT_JSON_INSTRUCTION, "\n上次输出解析失败，请仅返回合法 JSON。"]

        if self.include_last_error and last_error:
            # OutputParserException may include the model response, which can
            # contain a user's resume. Never send or log that content again.
            parts.append("\n上次输出未通过 JSON 格式或字段校验。")

        return "".join(parts)


structured_output_invoker = StructuredOutputInvoker()

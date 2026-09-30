import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OcrResult:
    text: str
    confidence: float | None
    pages: int
    provider: str
    error: str | None = None


class PaddleOcrProvider:
    name = "paddleocr"

    async def extract_text(self, file_bytes: bytes, filename: str) -> OcrResult:
        try:
            return await asyncio.to_thread(self._extract_sync, file_bytes, filename)
        except ImportError:
            return OcrResult("", None, 0, self.name, "未安装 PaddleOCR，跳过 OCR")
        except Exception as exc:
            logger.exception("OCR 处理失败: %s", filename)
            return OcrResult("", None, 0, self.name, str(exc))

    def _extract_sync(self, file_bytes: bytes, filename: str) -> OcrResult:
        from paddleocr import PaddleOCR
        import tempfile
        from pathlib import Path

        suffix = Path(filename).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix) as f:
            f.write(file_bytes)
            f.flush()
            engine = PaddleOCR(use_angle_cls=True, lang="ch")
            result = engine.predict(f.name)
        texts: list[str] = []
        scores: list[float] = []
        for page in result or []:
            data = page if isinstance(page, dict) else getattr(page, "json", lambda: {})()
            rec_texts = data.get("rec_texts", []) if isinstance(data, dict) else []
            rec_scores = data.get("rec_scores", []) if isinstance(data, dict) else []
            texts.extend(str(t) for t in rec_texts)
            scores.extend(float(s) for s in rec_scores)
        return OcrResult("\n".join(texts), sum(scores) / len(scores) if scores else None, 1, self.name)


class HttpOcrProvider:
    name = "paddleocr-http"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def extract_text(self, file_bytes: bytes, filename: str) -> OcrResult:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{self.base_url}/ocr",
                    files={"file": (filename, file_bytes)},
                )
                response.raise_for_status()
                data = response.json()
            return OcrResult(data.get("text", ""), data.get("confidence"), data.get("pages", 0), data.get("provider", self.name))
        except Exception as exc:
            logger.exception("HTTP OCR 服务调用失败: %s", filename)
            return OcrResult("", None, 0, self.name, str(exc))


def build_ocr_service():
    from app.config import settings
    return HttpOcrProvider(settings.ocr_service_url) if settings.ocr_provider == "http" else PaddleOcrProvider()


ocr_service = build_ocr_service()

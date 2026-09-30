import os
import json
os.environ.setdefault("FLAGS_use_mkldnn", "0")
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from paddleocr import PaddleOCR

app = FastAPI(title="Interview OCR Service")
ocr = PaddleOCR(
    lang=os.getenv("OCR_LANG", "ch"),
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    device="cpu",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    enable_mkldnn=False,
)


@app.get("/health")
async def health():
    return {"status": "UP", "service": "paddleocr"}


@app.post("/ocr")
async def recognize(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    suffix = Path(file.filename or ".bin").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix) as temp:
        temp.write(content)
        temp.flush()
        result = ocr.predict(temp.name)
    texts, scores = [], []
    for page in result or []:
        data = getattr(page, "json", page)
        data = data() if callable(data) else data
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}
        if isinstance(data, dict):
            data = data.get("res", data)
            texts.extend(data.get("rec_texts", []))
            scores.extend(data.get("rec_scores", []))
    return {
        "text": "\n".join(str(item) for item in texts),
        "confidence": sum(scores) / len(scores) if scores else None,
        "pages": len(result or []),
        "provider": "paddleocr",
    }

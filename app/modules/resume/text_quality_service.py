import re


class ResumeTextQualityService:
    def assess(self, text: str | None) -> dict:
        value = text or ""
        total = len(value)
        replacement = value.count("�")
        useful = len(re.findall(r"[\w\u4e00-\u9fff]", value))
        replacement_ratio = replacement / total if total else 1
        result = {"character_count": total, "useful_character_count": useful,
                  "replacement_character_count": replacement,
                  "useful_ratio": round(useful / total, 4) if total else 0.0,
                  "needs_ocr": total < 80 or useful < 40 or replacement_ratio > 0.05}
        result["status"] = "LOW" if result["needs_ocr"] else "PASS"
        return result


resume_text_quality_service = ResumeTextQualityService()

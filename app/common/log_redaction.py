import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:password|token|api[_-]?key|secret)\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[PHONE_REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL_REDACTED]"),
]


def redact_text(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if record.args:
            record.args = tuple(redact_text(str(arg)) for arg in record.args)
        return True


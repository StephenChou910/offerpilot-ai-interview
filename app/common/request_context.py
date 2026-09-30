"""Request-scoped context used for traceability without leaking state across requests."""

from contextvars import ContextVar

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return request_id_context.get()


def set_request_id(value: str):
    return request_id_context.set(value or "-")

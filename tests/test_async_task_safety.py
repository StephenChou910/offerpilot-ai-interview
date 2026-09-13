import os

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("AI_BAILIAN_API_KEY", "dummy-key")

from app.common.base_async_task import StreamTaskHandler
from app.common.model import AsyncTaskStatus
from app.infrastructure.redis.stream_worker import StreamWorker


class _FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _Task(StreamTaskHandler):
    def __init__(self, session):
        super().__init__(lambda: session)
        self.events = []

    @property
    def field_name(self):
        return "id"

    async def update_status(self, _db, _key, status, _error=None):
        self.events.append(status)

    async def process(self, db, _key):
        self.events.append("process")
        assert db.commits == 1


@pytest.mark.asyncio
async def test_task_commits_processing_status_before_long_running_work():
    session = _FakeSession()
    task = _Task(session)

    await task.handle({"id": "1"})

    assert task.events == [AsyncTaskStatus.PROCESSING, "process", AsyncTaskStatus.COMPLETED]
    assert session.commits == 2


class _FakeRedisService:
    def __init__(self):
        self.claimed_ids = []

    async def xpending_range(self, *_args, **_kwargs):
        return [
            {"message_id": "own", "consumer": "resume-worker-current"},
            {"message_id": "other", "consumer": "resume-worker-old"},
        ]

    async def xclaim(self, *_args, message_ids, **_kwargs):
        self.claimed_ids = message_ids
        return []


@pytest.mark.asyncio
async def test_pending_scanner_does_not_reclaim_its_own_active_message():
    redis = _FakeRedisService()
    worker = StreamWorker(name="resume-worker", redis_service=redis, stream_key="stream", handler=lambda _: None)
    worker._consumer_name = "resume-worker-current"

    await worker._retry_pending_messages()

    assert redis.claimed_ids == ["other"]

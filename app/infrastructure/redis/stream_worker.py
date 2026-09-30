from __future__ import annotations

import asyncio
import logging
import uuid
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.infrastructure.redis.redis_service import RedisService

logger = logging.getLogger(__name__)

T = TypeVar("T")
MessageHandler = Callable[[dict[str, str]], Awaitable[None]]
DeadLetterHandler = Callable[[dict[str, str], str], Awaitable[None]]

CONSUMER_GROUP = "default-workers"

# Pending 重试配置
PENDING_IDLE_THRESHOLD_MS = 60_000  # 消息空闲超过 60s 视为卡住
PENDING_CHECK_INTERVAL_S = 30  # 每 30s 扫描一次 pending list
MAX_DELIVERY_COUNT = 3  # 最多重试 3 次


class StreamWorker:
    def __init__(
        self,
        *,
        name: str,
        redis_service: RedisService,
        stream_key: str,
        handler: MessageHandler,
        cancel_handler: MessageHandler | None = None,
        dead_letter_handler: DeadLetterHandler | None = None,
        block_ms: int = 5000,
        read_count: int = 1,
        idle_sleep_seconds: float = 1.0,
        error_sleep_seconds: float = 3.0,
    ):
        self._name = name
        self._redis = redis_service
        self._stream_key = stream_key
        self._handler = handler
        self._cancel_handler = cancel_handler
        self._dead_letter_handler = dead_letter_handler
        self._block_ms = block_ms
        self._read_count = read_count
        self._idle_sleep_seconds = idle_sleep_seconds
        self._error_sleep_seconds = error_sleep_seconds
        self._consumer_name = f"{name}-{uuid.uuid4().hex[:8]}"
        self._stopped = False
        self._dead_letter_key = f"{stream_key}:dead_letter"
        self._retry_counts: dict[str, int] = {}  # msg_id → 已重试次数
        self._retry_not_before: dict[str, float] = {}
        self._active_messages: set[str] = set()

    def stop(self) -> None:
        self._stopped = True

    async def run_forever(self) -> None:
        await self._redis.xgroup_create(self._stream_key, CONSUMER_GROUP, id="0", mkstream=True)
        logger.info(
            "Starting async worker: %s (stream=%s, consumer=%s)", self._name, self._stream_key, self._consumer_name
        )

        # 主循环 + pending 扫描并发运行
        main_task = asyncio.create_task(self._read_new_messages())
        pending_task = asyncio.create_task(self._retry_pending_loop())

        try:
            await asyncio.gather(main_task, pending_task)
        except asyncio.CancelledError:
            main_task.cancel()
            pending_task.cancel()
            raise

    async def _read_new_messages(self) -> None:
        """主循环：读取新消息。"""
        while not self._stopped:
            try:
                results = await self._redis.xreadgroup(
                    CONSUMER_GROUP,
                    self._consumer_name,
                    {self._stream_key: ">"},
                    count=self._read_count,
                    block=self._block_ms,
                )
                if not results:
                    await asyncio.sleep(self._idle_sleep_seconds)
                    continue

                for _stream_name, stream_messages in results:
                    for msg_id, fields in stream_messages:
                        await self._process_message(msg_id, fields)

            except asyncio.CancelledError:
                logger.info("Async worker cancelled: %s", self._name)
                raise
            except TimeoutError:
                logger.warning(
                    "Async worker Redis Stream read timeout, will retry: %s (stream=%s, block_ms=%s)",
                    self._name,
                    self._stream_key,
                    self._block_ms,
                )
                await asyncio.sleep(self._error_sleep_seconds)
            except Exception:
                logger.exception("Async worker runtime error: %s", self._name)
                await asyncio.sleep(self._error_sleep_seconds)

    async def _retry_pending_loop(self) -> None:
        """后台循环：扫描 pending list，重试卡住的消息。"""
        while not self._stopped:
            try:
                await asyncio.sleep(PENDING_CHECK_INTERVAL_S)
                await self._retry_pending_messages()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pending 重试扫描失败: %s", self._name)

    async def _retry_pending_messages(self) -> None:
        """扫描 pending list，认领并重试空闲超时的消息。"""
        try:
            pending = await self._redis.xpending_range(
                self._stream_key,
                CONSUMER_GROUP,
                min="-",
                max="+",
                count=50,
            )
        except Exception as e:
            # consumer group 可能还不存在
            if "NOGROUP" in str(e):
                return
            raise

        if not pending:
            return

        # 只认领其他 consumer 遗留的消息。当前 consumer 正在处理耗时
        # LLM 任务时，不能把自己的 pending 消息再次认领并并发执行。
        idle_ids = [
            item["message_id"]
            for item in pending
            if item.get("consumer") != self._consumer_name
            or item["message_id"] in self._retry_counts
        ]
        if not idle_ids:
            return
        delivery_counts = {
            item["message_id"]: int(item.get("times_delivered", 0))
            for item in pending
        }

        # xclaim 认领空闲消息
        claimed = await self._redis.xclaim(
            self._stream_key,
            CONSUMER_GROUP,
            self._consumer_name,
            min_idle_time=PENDING_IDLE_THRESHOLD_MS,
            message_ids=idle_ids,
        )

        for msg_id, fields in claimed:
            if msg_id is None or fields is None:
                continue

            retry_count = max(
                self._retry_counts.get(msg_id, 0),
                max(0, delivery_counts.get(msg_id, 1) - 1),
            )
            if time.time() < self._retry_not_before.get(msg_id, 0):
                continue

            if retry_count >= MAX_DELIVERY_COUNT:
                # 超过最大重试次数，进入死信
                logger.warning(
                    "消息超过最大重试次数，进入死信: stream=%s, msg_id=%s, retries=%d",
                    self._stream_key,
                    msg_id,
                    retry_count,
                )
                await self._send_to_dead_letter(msg_id, fields, retry_count)
                if self._dead_letter_handler:
                    await self._dead_letter_handler(fields, "max_delivery_count_exceeded")
                await self._redis.xack(self._stream_key, CONSUMER_GROUP, msg_id)
                self._retry_counts.pop(msg_id, None)
                continue

            self._retry_counts[msg_id] = retry_count + 1
            self._retry_not_before[msg_id] = time.time() + min(300, 2 ** min(retry_count, 8))
            logger.info(
                "重试 pending 消息: stream=%s, msg_id=%s, retry=%d/%d",
                self._stream_key,
                msg_id,
                retry_count + 1,
                MAX_DELIVERY_COUNT,
            )
            await self._process_message(msg_id, fields)

    async def _process_message(self, msg_id: str, fields: dict[str, str]) -> None:
        """处理单条消息（成功则 ack，失败仅记录日志）。"""
        self._active_messages.add(msg_id)
        try:
            task_id = fields.get("_task_id")
            if task_id and await self._redis.exists(f"async-task:cancel:{task_id}"):
                logger.info("任务已取消，跳过业务处理: stream=%s, task_id=%s", self._stream_key, task_id)
                if self._cancel_handler:
                    await self._cancel_handler(fields)
                await self._redis.xack(self._stream_key, CONSUMER_GROUP, msg_id)
                self._retry_counts.pop(msg_id, None)
                return
            await self._handler(fields)
            await self._redis.xack(self._stream_key, CONSUMER_GROUP, msg_id)
            self._retry_counts.pop(msg_id, None)
            self._retry_not_before.pop(msg_id, None)
        except Exception:
            self._retry_counts.setdefault(msg_id, 0)
            logger.exception(
                "worker %s 处理消息失败: stream=%s, msg_id=%s",
                self._name,
                self._stream_key,
                msg_id,
            )
        finally:
            self._active_messages.discard(msg_id)

    async def _send_to_dead_letter(self, msg_id: str, fields: dict[str, str], delivery_count: int) -> None:
        """将消息发送到死信队列。"""
        try:
            dead_fields = {
                **fields,
                "_original_msg_id": msg_id,
                "_original_stream": self._stream_key,
                "_delivery_count": str(delivery_count),
                "_dead_letter_reason": "max_delivery_count_exceeded",
            }
            await self._redis.xadd(self._dead_letter_key, dead_fields, maxlen=1000)
            logger.info("消息已移入死信队列: msg_id=%s, stream=%s", msg_id, self._stream_key)
        except Exception:
            logger.exception("写入死信队列失败: msg_id=%s", msg_id)

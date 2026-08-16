"""Durable outbound Telegram delivery backed by SQLite and Redis Streams."""

from collections.abc import Awaitable, Callable
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
import hashlib
import os
from typing import Any

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from loguru import logger
from faststream import AckPolicy
from faststream.redis import StreamSub
from faststream.redis.annotations import Redis, RedisStreamMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.models import DeliveryJob
from database.repositories import DeliveryRepo


class DeliveryQueue:
    stream_name = "supportbots:telegram-delivery"
    consumer_group = "supportbots-delivery-workers"

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        broker: Any,
    ) -> None:
        self.session_factory = session_factory
        self.broker = broker
        self._worker_handlers: list[Any] = []
        self._reconcile_task: asyncio.Task[None] | None = None
        self._album_locks: dict[tuple[int, int, str, str], asyncio.Lock] = {}

    def _forget_album_lock(self, job: DeliveryJob) -> None:
        media_group_id = job.payload.get("media_group_id")
        if media_group_id is not None:
            self._album_locks.pop(
                (
                    job.bot_id,
                    job.source_chat_id,
                    str(media_group_id),
                    job.delivery_kind,
                ),
                None,
            )

    def register_worker(self, deliver: Callable[[dict], Awaitable[list[int]]]) -> None:
        new_jobs_stream = StreamSub(
            self.stream_name,
            group=self.consumer_group,
            consumer=f"supportbots-{os.getpid()}",
        )
        recovery_stream = StreamSub(
            self.stream_name,
            group=self.consumer_group,
            consumer=f"supportbots-{os.getpid()}-recovery",
            min_idle_time=60_000,
        )

        @self.broker.subscriber(
            stream=new_jobs_stream,
            ack_policy=AckPolicy.NACK_ON_ERROR,
            max_workers=40,
        )
        async def consume_new(
            job_id: int, message: RedisStreamMessage, redis: Redis
        ) -> None:
            await self.process(job_id, deliver, now=datetime.now())
            await message.delete(redis)

        @self.broker.subscriber(
            stream=recovery_stream,
            ack_policy=AckPolicy.NACK_ON_ERROR,
        )
        async def consume_recovered(
            job_id: int, message: RedisStreamMessage, redis: Redis
        ) -> None:
            await self.process(job_id, deliver, now=datetime.now())
            await message.delete(redis)

        self._worker_handlers = [consume_new, consume_recovered]

    async def start(self) -> None:
        await self.broker.start()
        self._reconcile_task = asyncio.create_task(self._reconcile_loop())

    async def stop(self) -> None:
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reconcile_task
            self._reconcile_task = None
        await self.broker.stop()

    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await self.reconcile(now=datetime.now())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("delivery reconciliation iteration failed")

    async def enqueue(
        self,
        *,
        bot_id: int,
        source_chat_id: int,
        source_message_id: int,
        delivery_kind: str,
        payload: dict,
    ) -> DeliveryJob:
        async with self.session_factory() as session:
            job = await DeliveryRepo(session).enqueue(
                bot_id=bot_id,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                delivery_kind=delivery_kind,
                payload=payload,
            )
        logger.info(
            "delivery job queued — job_id={}, bot_id={}, source_chat_id={}, "
            "source_message_id={}, kind={}",
            job.job_id,
            job.bot_id,
            job.source_chat_id,
            job.source_message_id,
            job.delivery_kind,
        )

        try:
            await self.broker.publish(str(job.job_id), stream=self.stream_name)
        except Exception as ex:
            logger.warning(
                "delivery job persisted but Redis publish failed — job_id={}: {}",
                job.job_id,
                ex,
            )
        else:
            async with self.session_factory() as session:
                await DeliveryRepo(session).mark_published(
                    job.job_id, published_at=datetime.now()
                )
        return job

    async def enqueue_album_item(
        self,
        *,
        bot_id: int,
        source_chat_id: int,
        media_group_id: str,
        delivery_kind: str,
        payload: dict,
    ) -> DeliveryJob:
        key = (bot_id, source_chat_id, media_group_id, delivery_kind)
        lock = self._album_locks.setdefault(key, asyncio.Lock())
        digest = hashlib.blake2b(media_group_id.encode(), digest_size=8).digest()
        source_message_id = int.from_bytes(digest, signed=True)
        async with lock:
            async with self.session_factory() as session:
                job = await DeliveryRepo(session).enqueue_album_item(
                    bot_id=bot_id,
                    source_chat_id=source_chat_id,
                    source_message_id=source_message_id,
                    delivery_kind=delivery_kind,
                    payload=payload,
                    ready_at=datetime.now() + timedelta(seconds=7),
                )
        incoming = payload["messages"][0]
        persisted_ids = {item["message_id"] for item in job.payload["messages"]}
        if incoming["message_id"] not in persisted_ids:
            if job.status in ("succeeded", "failed"):
                self._forget_album_lock(job)
            continuation_message = dict(incoming)
            continuation_message.pop("media_group_id", None)
            continuation_payload = {
                **payload,
                "operation": "resend_message_plus",
                "message": continuation_message,
            }
            continuation_payload.pop("messages", None)
            return await self.enqueue(
                bot_id=bot_id,
                source_chat_id=source_chat_id,
                source_message_id=incoming["message_id"],
                delivery_kind=f"{delivery_kind}:continuation",
                payload=continuation_payload,
            )
        logger.info(
            "delivery album item persisted — job_id={}, media_group_id={}, items={}",
            job.job_id,
            media_group_id,
            len(job.payload["messages"]),
        )
        return job

    async def process(
        self,
        job_id: int,
        deliver: Callable[[dict], Awaitable[list[int]]],
        *,
        now: datetime,
    ) -> bool:
        async with self.session_factory() as session:
            repo = DeliveryRepo(session)
            job = await repo.claim(job_id, now=now)
            if job is None:
                return False
            if job.lease_token is None:
                raise RuntimeError(f"delivery job {job.job_id} claimed without lease")
            lease_token = job.lease_token
            try:
                result_message_ids = await deliver(job.payload)
            except (
                TelegramBadRequest,
                TelegramForbiddenError,
                TelegramUnauthorizedError,
            ) as ex:
                updated = await repo.mark_failed(
                    job.job_id, lease_token=lease_token, error=str(ex)
                )
                self._forget_album_lock(job)
                if updated:
                    logger.error(
                        "delivery failed permanently — job_id={}, attempt={}: {}",
                        job.job_id,
                        job.attempt_count,
                        ex,
                    )
                return False
            except TelegramRetryAfter as ex:
                await repo.mark_retry(
                    job.job_id,
                    lease_token=lease_token,
                    error=str(ex),
                    next_attempt_at=now + timedelta(seconds=ex.retry_after),
                )
                return False
            except (TelegramNetworkError, TelegramServerError) as ex:
                updated = await repo.mark_retry(
                    job.job_id,
                    lease_token=lease_token,
                    error=str(ex),
                    next_attempt_at=now + timedelta(seconds=30 * job.attempt_count),
                )
                if updated:
                    logger.warning(
                        "delivery transport error; retry scheduled — job_id={}, attempt={}: {}",
                        job.job_id,
                        job.attempt_count,
                        ex,
                    )
                return False
            except Exception as ex:
                updated = await repo.mark_retry(
                    job.job_id,
                    lease_token=lease_token,
                    error=str(ex),
                    next_attempt_at=now + timedelta(seconds=30 * job.attempt_count),
                )
                if updated:
                    logger.exception(
                        "delivery worker error; retry scheduled — job_id={}", job.job_id
                    )
                return False

            updated = await repo.mark_succeeded(
                job.job_id,
                lease_token=lease_token,
                result_message_ids=result_message_ids,
            )
            if not updated:
                logger.warning(
                    "delivery result ignored after lease was superseded — job_id={}, attempt={}",
                    job.job_id,
                    job.attempt_count,
                )
                return False
            self._forget_album_lock(job)
            logger.info(
                "delivery job succeeded — job_id={}, attempt={}",
                job.job_id,
                job.attempt_count,
            )
            return True

    async def reconcile(self, *, now: datetime) -> int:
        async with self.session_factory() as session:
            jobs = await DeliveryRepo(session).list_due(now=now)
        published = 0
        for job in jobs:
            try:
                await self.broker.publish(str(job.job_id), stream=self.stream_name)
            except Exception as ex:
                logger.warning(
                    "delivery reconciliation publish failed — job_id={}: {}",
                    job.job_id,
                    ex,
                )
            else:
                async with self.session_factory() as session:
                    await DeliveryRepo(session).mark_published(
                        job.job_id, published_at=now
                    )
                published += 1
        return published

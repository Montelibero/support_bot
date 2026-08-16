import asyncio
import pytest
import importlib.util
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage
from faststream import AckPolicy
from datetime import datetime, timedelta
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import models, repositories
from bot import delivery_queue
from bot.routers import supports
from bot.routers.supports import cmd_resend


def test_delivery_job_model_exists():
    assert hasattr(models, "DeliveryJob")


def test_delivery_repository_exists():
    assert hasattr(repositories, "DeliveryRepo")


def test_delivery_queue_module_exists():
    assert importlib.util.find_spec("bot.delivery_queue") is not None


def test_delivery_queue_class_exists():
    assert hasattr(delivery_queue, "DeliveryQueue")


@pytest.mark.asyncio
async def test_enqueue_remains_pending_when_redis_publish_fails(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    broker = AsyncMock()
    broker.publish.side_effect = ConnectionError("redis unavailable")
    queue = delivery_queue.DeliveryQueue(session_factory, broker)

    job = await queue.enqueue(
        bot_id=10,
        source_chat_id=20,
        source_message_id=32,
        delivery_kind="support_forward",
        payload={"text": "saved first"},
    )

    async with session_factory() as session:
        saved = await repositories.DeliveryRepo(session).get(job.job_id)
    await engine.dispose()

    assert saved is not None
    assert saved.status == "pending"
    assert saved.payload == {"text": "saved first"}


@pytest.mark.asyncio
async def test_process_marks_successful_delivery(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    queue = delivery_queue.DeliveryQueue(session_factory, AsyncMock())
    job = await queue.enqueue(
        bot_id=10,
        source_chat_id=20,
        source_message_id=33,
        delivery_kind="support_forward",
        payload={"text": "deliver me"},
    )
    deliver = AsyncMock(return_value=[1002])

    processed = await queue.process(job.job_id, deliver, now=datetime.now())

    async with session_factory() as session:
        saved = await repositories.DeliveryRepo(session).get(job.job_id)
    await engine.dispose()

    assert processed is True
    deliver.assert_awaited_once_with({"text": "deliver me"})
    assert saved is not None
    assert saved.status == "succeeded"
    assert saved.result_message_ids == [1002]


@pytest.mark.asyncio
async def test_process_schedules_retry_after_telegram_network_error(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    queue = delivery_queue.DeliveryQueue(session_factory, AsyncMock())
    job = await queue.enqueue(
        bot_id=10,
        source_chat_id=20,
        source_message_id=34,
        delivery_kind="support_forward",
        payload={"text": "retry me"},
    )
    deliver = AsyncMock(
        side_effect=TelegramNetworkError(
            method=SendMessage(chat_id=1, text="test"), message="timeout"
        )
    )
    now = datetime.now()

    processed = await queue.process(job.job_id, deliver, now=now)

    async with session_factory() as session:
        saved = await repositories.DeliveryRepo(session).get(job.job_id)
    await engine.dispose()

    assert processed is False
    assert saved is not None
    assert saved.status == "retry"
    assert saved.attempt_count == 1
    assert saved.next_attempt_at is not None
    assert saved.next_attempt_at > now
    assert "timeout" in (saved.last_error or "").lower()


@pytest.mark.asyncio
async def test_process_marks_bad_request_as_permanent_failure(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    queue = delivery_queue.DeliveryQueue(session_factory, AsyncMock())
    job = await queue.enqueue(
        bot_id=10,
        source_chat_id=20,
        source_message_id=36,
        delivery_kind="support_forward",
        payload={"text": "bad destination"},
    )
    deliver = AsyncMock(
        side_effect=TelegramBadRequest(
            method=SendMessage(chat_id=1, text="test"), message="chat not found"
        )
    )

    processed = await queue.process(job.job_id, deliver, now=datetime.now())

    async with session_factory() as session:
        saved = await repositories.DeliveryRepo(session).get(job.job_id)
    await engine.dispose()

    assert processed is False
    assert saved is not None
    assert saved.status == "failed"
    assert "chat not found" in (saved.last_error or "").lower()


@pytest.mark.asyncio
async def test_reconcile_republishes_due_sqlite_jobs(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    broker = AsyncMock()
    broker.publish.side_effect = ConnectionError("redis unavailable")
    queue = delivery_queue.DeliveryQueue(session_factory, broker)
    job = await queue.enqueue(
        bot_id=10,
        source_chat_id=20,
        source_message_id=35,
        delivery_kind="support_forward",
        payload={"text": "recover me"},
    )
    broker.publish.reset_mock()
    broker.publish.side_effect = None

    published = await queue.reconcile(now=datetime.now())
    await engine.dispose()

    assert published == 1
    broker.publish.assert_awaited_once_with(str(job.job_id), stream=queue.stream_name)


@pytest.mark.asyncio
async def test_reconcile_does_not_duplicate_recent_successful_publish(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    broker = AsyncMock()
    queue = delivery_queue.DeliveryQueue(session_factory, broker)
    await queue.enqueue(
        bot_id=10,
        source_chat_id=20,
        source_message_id=38,
        delivery_kind="support_forward",
        payload={"text": "publish once"},
    )
    broker.publish.reset_mock()

    published = await queue.reconcile(now=datetime.now())
    await engine.dispose()

    assert published == 0
    broker.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_resend_does_not_call_get_me_for_logging():
    message = MagicMock()
    message.chat.id = 20
    message.chat.type = "group"
    bot = AsyncMock()
    bot.id = 10
    settings = MagicMock()
    settings.master_chat = 30

    await cmd_resend(
        message=message,
        bot=bot,
        repo=AsyncMock(),
        bot_settings=settings,
        config=MagicMock(),
    )

    bot.get_me.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_resend_persists_serialized_outbound_delivery():
    message = MagicMock()
    message.chat.id = 20
    message.message_id = 30
    message.photo = None
    message.media_group_id = None
    message.model_dump.return_value = {"message_id": 30, "chat": {"id": 20}}
    queue = AsyncMock()

    await supports.enqueue_resend_message_plus(
        delivery_queue=queue,
        message=message,
        bot_id=10,
        chat_id=40,
        text="hello",
        reply_to_message_id=None,
        support_user_id=50,
        message_thread_id=None,
        reply_markup=None,
    )

    queue.enqueue.assert_awaited_once_with(
        bot_id=10,
        source_chat_id=20,
        source_message_id=30,
        delivery_kind="resend:40",
        payload={
            "operation": "resend_message_plus",
            "bot_id": 10,
            "message": {"message_id": 30, "chat": {"id": 20}},
            "chat_id": 40,
            "text": "hello",
            "reply_to_message_id": None,
            "support_user_id": 50,
            "message_thread_id": None,
            "reply_markup": None,
        },
    )


@pytest.mark.asyncio
async def test_execute_delivery_payload_reconstructs_message_and_sends():
    bot = MagicMock()
    repo = MagicMock()
    config = MagicMock()
    payload = {
        "operation": "resend_message_plus",
        "bot_id": 10,
        "message": {
            "message_id": 30,
            "date": 1_700_000_000,
            "chat": {"id": 20, "type": "private"},
            "text": "source",
        },
        "chat_id": 40,
        "text": "hello",
        "reply_to_message_id": None,
        "support_user_id": 50,
        "message_thread_id": None,
        "reply_markup": None,
    }

    with patch.object(
        supports, "resend_message_plus", new=AsyncMock(return_value=[1003])
    ) as resend:
        result = await supports.execute_delivery_payload(payload, bot, repo, config)

    assert result == [1003]
    assert resend.await_args is not None
    call = resend.await_args.kwargs
    assert call["message"].message_id == 30
    assert call["chat_id"] == 40
    assert call["do_exception"] is True


@pytest.mark.asyncio
async def test_cmd_resend_enqueues_private_support_message():
    message = MagicMock()
    message.chat.id = 20
    message.chat.type = "private"
    message.message_id = 30
    message.photo = None
    message.media_group_id = None
    message.from_user.id = 20
    message.from_user.full_name = "User"
    message.reply_to_message = None
    message.html_text = "help"
    message.model_dump.return_value = {"message_id": 30, "chat": {"id": 20}}
    bot = MagicMock()
    bot.id = 10
    repo = AsyncMock()
    repo.has_user_received_reply.return_value = False
    settings = MagicMock()
    settings.username = "support_bot"
    settings.master_chat = 40
    settings.master_thread = None
    settings.block_links = False
    settings.ignore_users = []
    settings.use_auto_reply = False
    queue = AsyncMock()
    customization = AsyncMock()
    customization.get_extra_text.return_value = ""
    customization.get_reply_markup.return_value = None

    with patch.object(supports, "get_customization", return_value=customization):
        await cmd_resend(
            message=message,
            bot=bot,
            repo=repo,
            bot_settings=settings,
            config=MagicMock(),
            delivery_queue=queue,
        )

    queue.enqueue.assert_awaited_once()
    assert queue.enqueue.await_args.kwargs["delivery_kind"] == "resend:40"


def test_register_worker_nacks_unhandled_stream_errors():
    broker = MagicMock()
    broker.subscriber.return_value = lambda handler: handler
    queue = delivery_queue.DeliveryQueue(MagicMock(), broker)

    queue.register_worker(AsyncMock())

    new_jobs_call, recovery_call = broker.subscriber.call_args_list
    assert new_jobs_call.kwargs["ack_policy"] is AckPolicy.NACK_ON_ERROR
    assert new_jobs_call.kwargs["max_workers"] == 40
    assert new_jobs_call.kwargs["stream"].name == queue.stream_name
    assert new_jobs_call.kwargs["stream"].min_idle_time is None
    assert recovery_call.kwargs["ack_policy"] is AckPolicy.NACK_ON_ERROR
    assert recovery_call.kwargs["stream"].min_idle_time == 60_000


@pytest.mark.asyncio
async def test_consumed_stream_entry_is_deleted_after_sqlite_state_is_handled():
    broker = MagicMock()
    broker.subscriber.return_value = lambda handler: handler
    queue = delivery_queue.DeliveryQueue(MagicMock(), broker)
    queue.process = AsyncMock(return_value=True)
    queue.register_worker(AsyncMock())
    message = AsyncMock()
    redis = MagicMock()

    await queue._worker_handlers[0](42, message, redis)

    message.delete.assert_awaited_once_with(redis)


@pytest.mark.asyncio
async def test_queue_starts_and_stops_broker_lifecycle():
    broker = AsyncMock()
    queue = delivery_queue.DeliveryQueue(MagicMock(), broker)

    await queue.start()
    await queue.stop()

    broker.start.assert_awaited_once()
    broker.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_enqueue_returns_existing_job_for_duplicate_source(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    async with session_factory() as session:
        repo = repositories.DeliveryRepo(session)
        first = await repo.enqueue(
            bot_id=10,
            source_chat_id=20,
            source_message_id=30,
            delivery_kind="support_forward",
            payload={"text": "first"},
        )
        duplicate = await repo.enqueue(
            bot_id=10,
            source_chat_id=20,
            source_message_id=30,
            delivery_kind="support_forward",
            payload={"text": "duplicate"},
        )
        count = await session.scalar(select(func.count(models.DeliveryJob.job_id)))

    await engine.dispose()

    assert first.job_id == duplicate.job_id
    assert duplicate.payload == {"text": "first"}
    assert count == 1


@pytest.mark.asyncio
async def test_delivery_job_retry_and_success_transitions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    now = datetime.now()
    async with session_factory() as session:
        repo = repositories.DeliveryRepo(session)
        job = await repo.enqueue(
            bot_id=10,
            source_chat_id=20,
            source_message_id=31,
            delivery_kind="support_forward",
            payload={"text": "hello"},
        )

        claimed = await repo.claim(job.job_id, now=now)
        assert claimed is not None
        assert claimed.lease_token is not None
        first_attempt_count = claimed.attempt_count if claimed is not None else None
        already_claimed = await repo.claim(job.job_id, now=now)
        await repo.mark_retry(
            job.job_id,
            lease_token=claimed.lease_token,
            error="transport timeout",
            next_attempt_at=now + timedelta(minutes=1),
        )
        not_due = await repo.list_due(now=now)
        due = await repo.list_due(now=now + timedelta(minutes=2))
        claimed_again = await repo.claim(job.job_id, now=now + timedelta(minutes=2))
        assert claimed_again is not None
        assert claimed_again.lease_token is not None
        await repo.mark_succeeded(
            job.job_id,
            lease_token=claimed_again.lease_token,
            result_message_ids=[1001],
        )
        succeeded = await repo.get(job.job_id)

    await engine.dispose()

    assert claimed is not None
    assert first_attempt_count == 1
    assert already_claimed is None
    assert not_due == []
    assert [item.job_id for item in due] == [job.job_id]
    assert claimed_again is not None
    assert claimed_again.attempt_count == 2
    assert succeeded is not None
    assert succeeded.status == "succeeded"
    assert succeeded.result_message_ids == [1001]


@pytest.mark.asyncio
async def test_stale_processing_job_can_be_reclaimed_after_worker_crash(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    now = datetime.now()
    async with session_factory() as session:
        repo = repositories.DeliveryRepo(session)
        job = await repo.enqueue(
            bot_id=10,
            source_chat_id=20,
            source_message_id=37,
            delivery_kind="support_forward",
            payload={"text": "recover after crash"},
        )
        first = await repo.claim(job.job_id, now=now)
        due_after_crash = await repo.list_due(now=now + timedelta(minutes=2))
        reclaimed = await repo.claim(job.job_id, now=now + timedelta(minutes=2))

    await engine.dispose()

    assert first is not None
    assert [item.job_id for item in due_after_crash] == [job.job_id]
    assert reclaimed is not None
    assert reclaimed.attempt_count == 2


@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_reclaimed_job(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    now = datetime.now()
    async with session_factory() as session:
        repo = repositories.DeliveryRepo(session)
        job = await repo.enqueue(
            bot_id=10,
            source_chat_id=20,
            source_message_id=39,
            delivery_kind="support_forward",
            payload={"text": "fenced"},
        )
        first = await repo.claim(job.job_id, now=now)
        assert first is not None
        first_lease_token = first.lease_token
        assert first_lease_token is not None
        second = await repo.claim(job.job_id, now=now + timedelta(minutes=2))
        assert second is not None
        assert second.lease_token is not None

        stale_update = await repo.mark_retry(
            job.job_id,
            lease_token=first_lease_token,
            error="late timeout",
            next_attempt_at=now + timedelta(minutes=3),
        )
        current_update = await repo.mark_succeeded(
            job.job_id,
            lease_token=second.lease_token,
            result_message_ids=[2001],
        )
        saved = await repo.get(job.job_id)

    await engine.dispose()

    assert stale_update is False
    assert current_update is True
    assert saved is not None
    assert saved.status == "succeeded"


@pytest.mark.asyncio
async def test_reconcile_loop_survives_transient_repository_error():
    queue = delivery_queue.DeliveryQueue(MagicMock(), AsyncMock())
    queue.reconcile = AsyncMock(side_effect=[RuntimeError("sqlite busy"), 0])
    original_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        await original_sleep(0)

    with patch.object(
        delivery_queue.asyncio, "sleep", new=AsyncMock(side_effect=fast_sleep)
    ) as sleep:
        task = asyncio.create_task(queue._reconcile_loop())
        while queue.reconcile.await_count < 2:
            await original_sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert sleep.await_count >= 2


@pytest.mark.asyncio
async def test_album_items_are_merged_into_one_deferred_sqlite_job(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)

    queue = delivery_queue.DeliveryQueue(session_factory, AsyncMock())
    first = {"message_id": 101, "photo": [{"file_id": "a"}]}
    second = {"message_id": 102, "photo": [{"file_id": "b"}]}
    job = await queue.enqueue_album_item(
        bot_id=10,
        source_chat_id=20,
        media_group_id="9001",
        delivery_kind="resend_album:40",
        payload={"operation": "resend_media_group", "messages": [first]},
    )
    duplicate = await queue.enqueue_album_item(
        bot_id=10,
        source_chat_id=20,
        media_group_id="9001",
        delivery_kind="resend_album:40",
        payload={"operation": "resend_media_group", "messages": [second]},
    )

    async with session_factory() as session:
        saved = await repositories.DeliveryRepo(session).get(job.job_id)
    await engine.dispose()

    assert duplicate.job_id == job.job_id
    assert saved is not None
    assert [item["message_id"] for item in saved.payload["messages"]] == [101, 102]
    assert saved.next_attempt_at is not None


@pytest.mark.asyncio
async def test_album_payload_retry_keeps_all_items():
    bot = AsyncMock()
    bot.id = 10
    bot.send_media_group.side_effect = TelegramNetworkError(
        method=SendMessage(chat_id=1, text="test"), message="timeout"
    )
    payload = {
        "operation": "resend_media_group",
        "bot_id": 10,
        "messages": [
            {
                "message_id": 101,
                "date": 1_700_000_000,
                "chat": {"id": 20, "type": "private"},
                "media_group_id": "9001",
                "photo": [
                    {"file_id": "a", "file_unique_id": "ua", "width": 1, "height": 1}
                ],
            },
            {
                "message_id": 102,
                "date": 1_700_000_000,
                "chat": {"id": 20, "type": "private"},
                "media_group_id": "9001",
                "photo": [
                    {"file_id": "b", "file_unique_id": "ub", "width": 1, "height": 1}
                ],
            },
        ],
        "chat_id": 40,
        "text": "album",
        "reply_to_message_id": None,
        "support_user_id": None,
        "message_thread_id": None,
        "reply_markup": None,
    }

    with pytest.raises(TelegramNetworkError):
        await supports.execute_delivery_payload(payload, bot, AsyncMock(), MagicMock())

    media = bot.send_media_group.await_args.kwargs["media"]
    assert [item.media for item in media] == ["a", "b"]


@pytest.mark.asyncio
async def test_late_album_item_becomes_durable_continuation_job(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    broker = AsyncMock()
    queue = delivery_queue.DeliveryQueue(session_factory, broker)
    base = {
        "operation": "resend_media_group",
        "bot_id": 10,
        "chat_id": 40,
        "text": "album",
        "reply_to_message_id": None,
        "support_user_id": None,
        "message_thread_id": None,
        "reply_markup": None,
    }
    aggregate = await queue.enqueue_album_item(
        bot_id=10,
        source_chat_id=20,
        media_group_id="9002",
        delivery_kind="resend_album:40",
        payload={**base, "messages": [{"message_id": 201}]},
    )
    async with session_factory() as session:
        claimed = await repositories.DeliveryRepo(session).claim(
            aggregate.job_id, now=datetime.now() + timedelta(seconds=8)
        )
    assert claimed is not None

    continuation = await queue.enqueue_album_item(
        bot_id=10,
        source_chat_id=20,
        media_group_id="9002",
        delivery_kind="resend_album:40",
        payload={
            **base,
            "messages": [
                {
                    "message_id": 202,
                    "date": 1_700_000_000,
                    "chat": {"id": 20, "type": "private"},
                    "media_group_id": "9002",
                    "photo": [
                        {
                            "file_id": "late",
                            "file_unique_id": "u-late",
                            "width": 1,
                            "height": 1,
                        }
                    ],
                }
            ],
        },
    )
    async with session_factory() as session:
        count = await session.scalar(select(func.count(models.DeliveryJob.job_id)))
    await engine.dispose()

    assert continuation.job_id != aggregate.job_id
    assert continuation.payload["operation"] == "resend_message_plus"
    assert continuation.payload["message"]["message_id"] == 202
    assert "media_group_id" not in continuation.payload["message"]
    assert count == 2

    bot = AsyncMock()
    bot.id = 10
    bot.send_photo.return_value = MagicMock(message_id=401, chat=MagicMock(id=40))
    bot.send_message.return_value = MagicMock(message_id=402, chat=MagicMock(id=40))
    await supports.execute_delivery_payload(
        continuation.payload, bot, AsyncMock(), MagicMock()
    )
    bot.send_photo.assert_awaited_once()
    bot.send_media_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_album_deleted_reply_retries_full_group_without_reply():
    bot = AsyncMock()
    bot.id = 10
    sent_items = [
        MagicMock(message_id=301, chat=MagicMock(id=40)),
        MagicMock(message_id=302, chat=MagicMock(id=40)),
    ]
    bot.send_media_group.side_effect = [
        TelegramBadRequest(
            method=SendMessage(chat_id=40, text="test"),
            message="message to be replied not found",
        ),
        sent_items,
    ]
    bot.send_message.return_value = MagicMock(message_id=303, chat=MagicMock(id=40))
    payload = {
        "operation": "resend_media_group",
        "bot_id": 10,
        "messages": [
            {
                "message_id": message_id,
                "date": 1_700_000_000,
                "chat": {"id": 20, "type": "private"},
                "media_group_id": "9003",
                "photo": [
                    {
                        "file_id": file_id,
                        "file_unique_id": f"u{file_id}",
                        "width": 1,
                        "height": 1,
                    }
                ],
            }
            for message_id, file_id in ((301, "a"), (302, "b"))
        ],
        "chat_id": 40,
        "text": "album",
        "reply_to_message_id": 99,
        "support_user_id": None,
        "message_thread_id": None,
        "reply_markup": None,
    }

    result = await supports.execute_delivery_payload(
        payload, bot, AsyncMock(), MagicMock()
    )

    assert result == [301, 302, 303]
    assert bot.send_media_group.await_count == 2
    assert bot.send_media_group.await_args.kwargs["reply_to_message_id"] is None

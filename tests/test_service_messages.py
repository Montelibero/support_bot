"""Tests for the bot-authored (service) message registry.

Covers the session middleware that records messages the bot posts to a master
chat on its own initiative, and the core behaviour that stays silent when an
operator replies to such a message instead of a forwarded user message.
"""

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from sqlalchemy import select
from aiogram.methods import (
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)

import bot.service_messages as service_messages
from bot.routers.supports import router as support_router
from bot.service_messages import (
    ServiceMessageRecorder,
    delivery_in_progress,
    install_recorder,
    registration_enabled,
    suppress_registration,
)
from tests.conftest import MOCK_SERVER_URL, TEST_BOT_TOKEN

MASTER_CHAT_ID = -100123456789
BOT_ID = 123456


def _message(message_id: int, chat_id: int) -> types.Message:
    return types.Message(
        message_id=message_id,
        date=datetime.datetime.now(),
        chat=types.Chat(id=chat_id, type="supergroup"),
        from_user=types.User(id=BOT_ID, is_bot=True, first_name="Test Bot"),
        text="service",
    )


async def _run_recorder(method: TelegramMethod, result: Any) -> None:
    recorder = ServiceMessageRecorder()
    bot = Bot(token=TEST_BOT_TOKEN, session=AiohttpSession())

    async def make_request(bot: Bot, method: TelegramMethod) -> Any:
        return result

    try:
        await recorder(make_request, bot, method)
    finally:
        await bot.session.close()


@pytest.fixture
def registered(monkeypatch):
    calls: list[tuple[int, int, int]] = []

    async def fake_register(bot_id, chat_id, message_id):
        calls.append((bot_id, chat_id, message_id))

    monkeypatch.setattr(service_messages, "register_bot_message", fake_register)
    monkeypatch.setattr(service_messages, "is_master_chat", lambda b, c: True)
    return calls


@pytest.mark.asyncio
async def test_records_message_sent_to_master_chat(registered):
    method = SendMessage(chat_id=MASTER_CHAT_ID, text="Тикет 1 закрыт")
    await _run_recorder(method, _message(555, MASTER_CHAT_ID))

    assert registered == [(BOT_ID, MASTER_CHAT_ID, 555)]


@pytest.mark.asyncio
async def test_records_every_album_item(registered):
    method = SendPhoto(chat_id=MASTER_CHAT_ID, photo="file-id")
    album = [_message(556, MASTER_CHAT_ID), _message(557, MASTER_CHAT_ID)]
    await _run_recorder(method, album)

    assert registered == [
        (BOT_ID, MASTER_CHAT_ID, 556),
        (BOT_ID, MASTER_CHAT_ID, 557),
    ]


@pytest.mark.asyncio
async def test_skips_non_master_chat(registered, monkeypatch):
    monkeypatch.setattr(service_messages, "is_master_chat", lambda b, c: False)
    method = SendMessage(chat_id=111222, text="reply to user")
    await _run_recorder(method, _message(558, 111222))

    assert registered == []


@pytest.mark.asyncio
async def test_skips_delivered_user_copies(registered):
    method = SendMessage(chat_id=MASTER_CHAT_ID, text="forwarded ticket")

    @suppress_registration
    async def deliver():
        assert delivery_in_progress() is True
        await _run_recorder(method, _message(559, MASTER_CHAT_ID))

    assert delivery_in_progress() is False
    await deliver()
    assert delivery_in_progress() is False
    assert registered == []


@pytest.mark.asyncio
async def test_skips_methods_without_message_result(registered):
    method = SendMessage(chat_id=MASTER_CHAT_ID, text="text")
    await _run_recorder(method, True)

    assert registered == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        EditMessageText(chat_id=MASTER_CHAT_ID, message_id=26905, text="edited"),
        EditMessageReplyMarkup(chat_id=MASTER_CHAT_ID, message_id=26905),
    ],
)
async def test_skips_message_edits(registered, method):
    """Edit methods return the *existing* message, not a new bot message.

    `/take` and `/ticket_close` edit the ticket keyboard, so registering those
    results would mark forwarded user messages as technical and suppress the
    lost-history notice.
    """
    await _run_recorder(method, _message(26905, MASTER_CHAT_ID))

    assert registered == []


@pytest.mark.asyncio
async def test_failure_notice_inside_delivery_is_registered(registered):
    """Delivery failure notices are service messages and must be recorded."""
    method = SendMessage(chat_id=MASTER_CHAT_ID, text="Ошибка отправки")

    @suppress_registration
    async def deliver():
        with registration_enabled():
            await _run_recorder(method, _message(561, MASTER_CHAT_ID))

    await deliver()

    assert registered == [(BOT_ID, MASTER_CHAT_ID, 561)]


@pytest.mark.asyncio
async def test_registry_failure_never_breaks_send(monkeypatch):
    async def failing_register(bot_id, chat_id, message_id):
        raise RuntimeError("db is down")

    monkeypatch.setattr(service_messages, "register_bot_message", failing_register)
    monkeypatch.setattr(service_messages, "is_master_chat", lambda b, c: True)

    method = SendMessage(chat_id=MASTER_CHAT_ID, text="Тикет 1 закрыт")
    # Must not raise: the outbound call already succeeded.
    await _run_recorder(method, _message(560, MASTER_CHAT_ID))


@pytest.mark.asyncio
async def test_install_recorder_is_idempotent():
    bot = Bot(token=TEST_BOT_TOKEN, session=AiohttpSession())
    try:
        install_recorder(bot)
        install_recorder(bot)

        assert len(bot.session.middleware) == 1
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_make_bot_installs_recorder():
    from config.bot_config import make_bot

    bot = make_bot(TEST_BOT_TOKEN)
    try:
        assert any(
            isinstance(m, ServiceMessageRecorder) for m in bot.session.middleware
        )
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_copy_message_results_are_recognized(registered):
    """`CopyMessage`/`CopyMessages` return `MessageId` values, not `Message`."""
    method = SendMessage(chat_id=MASTER_CHAT_ID, text="copied")
    await _run_recorder(method, types.MessageId(message_id=562))
    await _run_recorder(
        method,
        [
            types.MessageId(message_id=563),
            types.MessageId(message_id=564),
        ],
    )

    assert registered == [
        (BOT_ID, MASTER_CHAT_ID, 562),
        (BOT_ID, MASTER_CHAT_ID, 563),
        (BOT_ID, MASTER_CHAT_ID, 564),
    ]


def test_edit_method_list_covers_every_edit_method():
    """New `EditMessage*` methods must not register the message they edit."""
    from aiogram import methods as aiogram_methods

    expected = {
        name
        for name, obj in vars(aiogram_methods).items()
        if isinstance(obj, type)
        and (name.startswith("EditMessage") or name == "StopMessageLiveLocation")
    }
    covered = {cls.__name__ for cls in service_messages._EDIT_METHODS}

    assert expected == covered


def test_is_master_chat_matches_configured_bot(monkeypatch):
    settings = MagicMock()
    settings.master_chat = MASTER_CHAT_ID
    monkeypatch.setattr(
        service_messages.bot_config,
        "get_bot_setting",
        lambda bot_id: settings if bot_id == BOT_ID else None,
    )

    assert service_messages.is_master_chat(BOT_ID, MASTER_CHAT_ID) is True
    assert service_messages.is_master_chat(BOT_ID, 111222) is False
    assert service_messages.is_master_chat(999, MASTER_CHAT_ID) is False


@pytest.mark.asyncio
async def test_recorder_writes_to_real_sqlite(sqlite_db, monkeypatch):
    """End-to-end: middleware → save_service_message → Repo lookup."""
    from database.repositories import Repo

    models = sqlite_db
    monkeypatch.setattr(service_messages, "is_master_chat", lambda b, c: True)
    await _run_recorder(
        SendMessage(chat_id=MASTER_CHAT_ID, text="Тикет 1 закрыт"),
        _message(565, MASTER_CHAT_ID),
    )

    async with models.session_maker() as session:
        repo = Repo(session)
        assert await repo.get_service_message(BOT_ID, MASTER_CHAT_ID, 565) is not None


def _master_settings() -> MagicMock:
    settings = MagicMock()
    settings.master_chat = MASTER_CHAT_ID
    settings.master_thread = None
    settings.ignore_commands = False
    settings.block_links = False
    settings.ignore_users = []
    settings.special_commands = 0
    settings.use_auto_reply = False
    settings.mark_bad = False
    settings.use_local_names = True
    settings.local_names = {"888999": "Agent007"}
    return settings


async def _feed_master_reply(
    repo,
    mock_server,
    *,
    reply_to_message_id: int,
    reply_text: str,
    edited: bool = False,
    settings: MagicMock | None = None,
) -> None:
    """Feed one master-chat reply (or edit) from an operator."""
    session = AiohttpSession(api=TelegramAPIServer.from_base(MOCK_SERVER_URL))
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(support_router)

    from aiogram import BaseMiddleware

    class MockMiddleware(BaseMiddleware):
        def __init__(self):
            self.settings = settings or _master_settings()
            self.config = MagicMock()
            self.config.get_bot_setting.return_value = self.settings
            self.config.update_bot_setting = AsyncMock()
            self.config.media_groups = {}

        async def __call__(self, handler, event, data):
            data["repo"] = repo
            data["config"] = self.config
            data["bot_settings"] = self.settings
            return await handler(event, data)

    dp.update.middleware(MockMiddleware())
    mock_server.clear()

    operator_message = types.Message(
        message_id=700,
        date=datetime.datetime.now(),
        chat=types.Chat(id=MASTER_CHAT_ID, type="supergroup"),
        from_user=types.User(
            id=888999, is_bot=False, first_name="Admin", username="admin_tg"
        ),
        reply_to_message=types.Message(
            message_id=reply_to_message_id,
            date=datetime.datetime.now(),
            chat=types.Chat(id=MASTER_CHAT_ID, type="supergroup"),
            from_user=types.User(
                id=BOT_ID, is_bot=True, first_name="Test Bot", username="test_bot"
            ),
            text="Тикет 26905 закрыт (взял @decentral)",
        ),
        text=reply_text,
    )
    update = types.Update(
        update_id=1,
        message=None if edited else operator_message,
        edited_message=operator_message if edited else None,
    )
    try:
        await dp.feed_update(bot=bot, update=update)
    finally:
        await bot.session.close()


def _master_chat_texts(mock_server) -> list[str]:
    return [
        str(r["data"].get("text", ""))
        for r in mock_server
        if r["method"] == "sendMessage"
        and str(r["data"].get("chat_id")) == str(MASTER_CHAT_ID)
    ]


@pytest.mark.asyncio
async def test_reply_to_service_message_is_silent(repo, mock_server):
    repo.service_messages[(BOT_ID, MASTER_CHAT_ID, 26905)] = MagicMock()

    await _feed_master_reply(
        repo, mock_server, reply_to_message_id=26905, reply_text="ok"
    )

    assert mock_server == []


@pytest.mark.asyncio
async def test_reply_to_unknown_bot_message_still_reports_lost_history(
    repo, mock_server
):
    await _feed_master_reply(
        repo, mock_server, reply_to_message_id=99999, reply_text="ok"
    )

    texts = _master_chat_texts(mock_server)
    assert any("Не найдена история переписки" in text for text in texts)


@pytest.mark.asyncio
async def test_reply_to_forwarded_user_message_is_still_delivered(repo, mock_server):
    user_chat_id = 111222
    repo.messages.append(
        {
            "bot_id": BOT_ID,
            "user_id": user_chat_id,
            "message_id": 100,
            "resend_id": 26905,
            "chat_from_id": user_chat_id,
            "chat_for_id": MASTER_CHAT_ID,
        }
    )

    await _feed_master_reply(
        repo, mock_server, reply_to_message_id=26905, reply_text="Everything is fine"
    )

    delivered = [
        r
        for r in mock_server
        if r["method"] == "sendMessage"
        and str(r["data"].get("chat_id")) == str(user_chat_id)
    ]
    assert delivered, "reply was not delivered to the user"
    assert "Вам ответил Agent007" in str(delivered[0]["data"]["text"])


@pytest.mark.asyncio
async def test_reply_to_service_message_is_silent_without_operator_name(
    repo, mock_server
):
    """Silence must not depend on the operator having a pseudonym."""
    repo.service_messages[(BOT_ID, MASTER_CHAT_ID, 26905)] = MagicMock()
    settings = _master_settings()
    settings.local_names = {}

    await _feed_master_reply(
        repo,
        mock_server,
        reply_to_message_id=26905,
        reply_text="ok",
        settings=settings,
    )

    assert mock_server == []


@pytest.mark.asyncio
async def test_edit_of_reply_to_service_message_is_silent(repo, mock_server):
    repo.service_messages[(BOT_ID, MASTER_CHAT_ID, 26905)] = MagicMock()

    await _feed_master_reply(
        repo,
        mock_server,
        reply_to_message_id=26905,
        reply_text="ok, fixed a typo",
        edited=True,
    )

    assert mock_server == []


@pytest.mark.asyncio
async def test_edit_of_lost_history_reply_still_reports_failure(repo, mock_server):
    await _feed_master_reply(
        repo,
        mock_server,
        reply_to_message_id=99999,
        reply_text="ok, fixed a typo",
        edited=True,
    )

    texts = _master_chat_texts(mock_server)
    assert any("Не удалось отправить изменения" in text for text in texts)


# --- real SQLite layer -------------------------------------------------------


@pytest.fixture
async def sqlite_db(tmp_path, monkeypatch):
    """Point database.models at a throwaway SQLite file."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from database import models

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'support.db'}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(models, "engine", engine)
    monkeypatch.setattr(models, "session_maker", maker)
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    try:
        yield models
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_save_service_message_is_idempotent(sqlite_db):
    from database.repositories import Repo

    models = sqlite_db
    await models.save_service_message(BOT_ID, MASTER_CHAT_ID, 26905)
    await models.save_service_message(BOT_ID, MASTER_CHAT_ID, 26905)

    async with models.session_maker() as session:
        repo = Repo(session)
        assert await repo.get_service_message(BOT_ID, MASTER_CHAT_ID, 26905) is not None
        assert await repo.get_service_message(BOT_ID, MASTER_CHAT_ID, 26906) is None

    async with models.session_maker() as session:
        rows = (await session.execute(select(models.ServiceMessages))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_update_db_prunes_old_service_messages(sqlite_db):
    models = sqlite_db
    await models.save_service_message(BOT_ID, MASTER_CHAT_ID, 1)
    async with models.session_maker() as session:
        session.add(
            models.ServiceMessages(
                bot_id=BOT_ID,
                chat_id=MASTER_CHAT_ID,
                message_id=2,
                record_add_date=datetime.datetime.now() - datetime.timedelta(days=200),
            )
        )
        await session.commit()

    await models.update_db()

    async with models.session_maker() as session:
        remaining = (
            (await session.execute(select(models.ServiceMessages.message_id)))
            .scalars()
            .all()
        )
    assert list(remaining) == [1]

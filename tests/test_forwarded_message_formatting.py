import datetime
from unittest.mock import MagicMock

import pytest
from aiogram import BaseMiddleware, Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import MessageOriginType

from bot.routers.supports import router as support_router
from tests.conftest import MOCK_SERVER_URL, TEST_BOT_TOKEN


class MockMiddleware(BaseMiddleware):
    def __init__(self, repo_instance, config_instance, settings_instance):
        self.repo = repo_instance
        self.config = config_instance
        self.settings = settings_instance

    async def __call__(self, handler, event, data):
        data["repo"] = self.repo
        data["config"] = self.config
        data["bot_settings"] = self.settings
        return await handler(event, data)


def build_settings(master_chat_id: int) -> tuple[MagicMock, MagicMock]:
    mock_config = MagicMock()
    mock_settings = MagicMock()
    mock_settings.master_chat = master_chat_id
    mock_settings.ignore_commands = False
    mock_settings.master_thread = None
    mock_settings.block_links = False
    mock_settings.ignore_users = []
    mock_settings.special_commands = 0
    mock_settings.use_auto_reply = False
    mock_settings.auto_reply = ""
    mock_settings.mark_bad = False

    mock_config.get_bot_setting.return_value = mock_settings
    mock_config.media_groups = {}
    return mock_config, mock_settings


def build_dispatcher(repo, master_chat_id: int) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(support_router)
    mock_config, mock_settings = build_settings(master_chat_id)
    dp.update.middleware(MockMiddleware(repo, mock_config, mock_settings))
    return dp


@pytest.mark.asyncio
async def test_forwarded_message_is_marked_in_master_chat(mock_server, repo):
    session = AiohttpSession(api=TelegramAPIServer.from_base(MOCK_SERVER_URL))
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dp = build_dispatcher(repo, master_chat_id=-100123456789)

    update_ticket = types.Update(
        update_id=1,
        message=types.Message(
            message_id=100,
            date=datetime.datetime.now(),
            chat=types.Chat(id=111222, type="private"),
            from_user=types.User(
                id=111222, is_bot=False, first_name="Test", username="test_user"
            ),
            text="Посмотри, что мне прислали",
            forward_origin=types.MessageOriginUser(
                type=MessageOriginType.USER,
                date=datetime.datetime.now(),
                sender_user=types.User(id=999888, is_bot=False, first_name="Alice"),
            ),
        ),
    )

    await dp.feed_update(bot=bot, update=update_ticket)

    req_master = next(
        (
            r
            for r in mock_server
            if r["method"] == "sendMessage"
            and str(r["data"]["chat_id"]) == "-100123456789"
        ),
        None,
    )
    assert req_master is not None
    text = req_master["data"]["text"]
    assert "<b>Пересланное сообщение</b>" in text
    assert "Источник: пользователь Alice" in text
    assert "Посмотри, что мне прислали" in text

    await bot.session.close()


@pytest.mark.asyncio
async def test_forwarded_message_uses_legacy_hidden_sender_name(mock_server, repo):
    session = AiohttpSession(api=TelegramAPIServer.from_base(MOCK_SERVER_URL))
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dp = build_dispatcher(repo, master_chat_id=-100123456789)

    update_ticket = types.Update(
        update_id=1,
        message=types.Message(
            message_id=101,
            date=datetime.datetime.now(),
            chat=types.Chat(id=111222, type="private"),
            from_user=types.User(
                id=111222, is_bot=False, first_name="Test", username="test_user"
            ),
            text="Скрытый форвард",
            forward_sender_name="Hidden Name",
        ),
    )

    await dp.feed_update(bot=bot, update=update_ticket)

    req_master = next(
        (
            r
            for r in mock_server
            if r["method"] == "sendMessage"
            and str(r["data"]["chat_id"]) == "-100123456789"
        ),
        None,
    )
    assert req_master is not None
    text = req_master["data"]["text"]
    assert "<b>Пересланное сообщение</b>" in text
    assert "Источник: скрытый отправитель Hidden Name" in text

    await bot.session.close()


@pytest.mark.asyncio
async def test_edited_forwarded_message_keeps_forwarded_marker(mock_server, repo):
    session = AiohttpSession(api=TelegramAPIServer.from_base(MOCK_SERVER_URL))
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    master_chat_id = -100123456789
    dp = build_dispatcher(repo, master_chat_id=master_chat_id)

    await dp.feed_update(
        bot=bot,
        update=types.Update(
            update_id=1,
            message=types.Message(
                message_id=150,
                date=datetime.datetime.now(),
                chat=types.Chat(id=111222, type="private"),
                from_user=types.User(
                    id=111222, is_bot=False, first_name="Test", username="test_user"
                ),
                text="Оригинал",
                forward_origin=types.MessageOriginChat(
                    type=MessageOriginType.CHAT,
                    date=datetime.datetime.now(),
                    sender_chat=types.Chat(
                        id=-10077, type="supergroup", title="News Chat"
                    ),
                    author_signature=None,
                ),
            ),
        ),
    )

    mock_server.clear()

    await dp.feed_update(
        bot=bot,
        update=types.Update(
            update_id=2,
            edited_message=types.Message(
                message_id=150,
                date=datetime.datetime.now(),
                edit_date=int(datetime.datetime.now().timestamp()),
                chat=types.Chat(id=111222, type="private"),
                from_user=types.User(
                    id=111222, is_bot=False, first_name="Test", username="test_user"
                ),
                text="Оригинал, но отредактирован",
                forward_origin=types.MessageOriginChat(
                    type=MessageOriginType.CHAT,
                    date=datetime.datetime.now(),
                    sender_chat=types.Chat(
                        id=-10077, type="supergroup", title="News Chat"
                    ),
                    author_signature=None,
                ),
            ),
        ),
    )

    req_master = next(
        (
            r
            for r in mock_server
            if r["method"] == "sendMessage"
            and str(r["data"]["chat_id"]) == str(master_chat_id)
        ),
        None,
    )
    assert req_master is not None
    text = req_master["data"]["text"]
    assert "<b>Пересланное сообщение</b>" in text
    assert "Источник: чат News Chat" in text
    assert "отредактировано" in text

    await bot.session.close()

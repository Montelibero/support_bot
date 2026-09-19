"""Regression: delivery-queue enqueue serialization must survive real messages.

aiogram fills optional fields Telegram did not send (e.g. LinkPreviewOptions
on incoming messages) with Default sentinels; Message.model_dump raises
PydanticSerializationError on them, which killed every reply and user forward
in webhook mode where the delivery queue is active.
"""

import datetime
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from bot.routers.supports import _serialize_message_payload, router as support_router
from tests.conftest import MOCK_SERVER_URL, TEST_BOT_TOKEN


def _user_message_update() -> types.Update:
    # Real-shape Telegram update: link_preview_options present, the optional
    # fields inside it not sent — aiogram fills them with Default sentinels.
    return types.Update(
        update_id=1,
        message=types.Message.model_validate(
            {
                "message_id": 777,
                "from": {
                    "id": 777,
                    "is_bot": False,
                    "first_name": "User",
                    "username": "user",
                },
                "chat": {"id": 777, "type": "private"},
                "date": int(datetime.datetime.now().timestamp()),
                "text": "привет https://example.com",
                "link_preview_options": {
                    "url": "https://example.com",
                    "prefer_large_media": True,
                },
            }
        ),
    )


def test_serialize_message_payload_neutralizes_default_sentinels():
    message = _user_message_update().message

    payload = _serialize_message_payload(message)

    json.dumps(payload)  # payload must be plain JSON
    assert payload["link_preview_options"]["url"] == "https://example.com"
    assert payload["link_preview_options"]["is_disabled"] is None
    restored = types.Message.model_validate(payload)
    assert restored.text == "привет https://example.com"


def test_serialize_message_payload_keeps_forward_origin_discriminator():
    message = types.Message.model_validate(
        {
            "message_id": 900,
            "from": {"id": 42, "is_bot": False, "first_name": "User"},
            "chat": {"id": 42, "type": "private"},
            "date": 1700000000,
            "forward_origin": {
                "type": "user",
                "date": 1699990000,
                "sender_user": {"id": 7, "is_bot": False, "first_name": "Fwd"},
            },
            "text": "пересылка",
        }
    )

    payload = _serialize_message_payload(message)

    assert payload["forward_origin"]["type"] == "user"


@pytest.mark.asyncio
async def test_user_message_with_link_preview_reaches_delivery_queue(mock_server, repo):
    session = AiohttpSession(api=TelegramAPIServer.from_base(MOCK_SERVER_URL))
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(support_router)

    mock_config = MagicMock()
    mock_settings = MagicMock()
    mock_settings.master_chat = -100999
    mock_settings.username = "helper"
    mock_settings.block_links = False
    mock_settings.ignore_users = []
    mock_settings.spam_block_words = []
    mock_settings.ignore_cjk_messages = False
    mock_settings.use_auto_reply = False
    mock_settings.mark_bad = False
    mock_settings.master_thread = None
    mock_config.get_bot_setting.return_value = mock_settings

    from aiogram import BaseMiddleware

    class MockMiddleware(BaseMiddleware):
        def __init__(self):
            self.repo = repo
            self.config = mock_config
            self.settings = mock_settings

        async def __call__(self, handler, event, data):
            data["repo"] = self.repo
            data["config"] = self.config
            data["bot_settings"] = self.settings
            return await handler(event, data)

    dp.update.middleware(MockMiddleware())

    queue = MagicMock()
    queue.enqueue = AsyncMock(return_value=None)

    await dp.feed_update(bot=bot, update=_user_message_update(), delivery_queue=queue)

    queue.enqueue.assert_awaited_once()
    payload = queue.enqueue.await_args.kwargs["payload"]
    assert payload["message"]["link_preview_options"]["url"] == "https://example.com"
    types.Message.model_validate(payload["message"])  # worker round-trip
    await bot.session.close()

"""Regression: delivery-queue enqueue payloads stay lean and JSON-safe.

The queue persists only the explicit content contract (ids + media
references), never the whole aiogram Message — whole-message dumps once
raised PydanticSerializationError on aiogram's Default sentinels inside
LinkPreviewOptions and killed every reply in webhook mode.
"""

import datetime
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from bot.routers.supports import _delivery_content, router as support_router
from tests.conftest import MOCK_SERVER_URL, TEST_BOT_TOKEN


def _text_message() -> types.Message:
    return types.Message.model_validate(
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
    )


def test_delivery_content_keeps_only_ids_and_media_references():
    content = _delivery_content(_text_message())

    assert content["message_id"] == 777
    assert content["chat_id"] == 777
    assert content["photo_file_id"] is None
    assert content["document_file_id"] is None
    assert content["location"] is None
    assert content["contact"] is None
    assert content["venue"] is None
    # the contract must not carry the message text or sender objects
    assert "text" not in content
    assert "from_user" not in content
    assert "link_preview_options" not in content


def test_delivery_content_extracts_media_and_geo_fields():
    message = types.Message.model_validate(
        {
            "message_id": 900,
            "from": {"id": 42, "is_bot": False, "first_name": "User"},
            "chat": {"id": 42, "type": "private"},
            "date": 1700000000,
            "photo": [
                {"file_id": "small", "file_unique_id": "s", "width": 90, "height": 90},
                {
                    "file_id": "big",
                    "file_unique_id": "b",
                    "width": 1280,
                    "height": 960,
                },
            ],
            "media_group_id": "12345",
            "location": {"latitude": 25.2, "longitude": 55.3},
        }
    )

    content = _delivery_content(message)

    assert content["photo_file_id"] == "big"  # largest size, like the old path
    assert content["media_group_id"] == "12345"
    assert content["location"] == {"latitude": 25.2, "longitude": 55.3}
    json.dumps(content)


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

    update = types.Update(update_id=1, message=_text_message())
    await dp.feed_update(bot=bot, update=update, delivery_queue=queue)

    queue.enqueue.assert_awaited_once()
    payload = queue.enqueue.await_args.kwargs["payload"]
    assert payload["payload_version"] == 2
    assert payload["content"]["message_id"] == 777
    serialized = json.dumps(payload)
    assert "link_preview_options" not in serialized
    assert "from_user" not in serialized
    await bot.session.close()

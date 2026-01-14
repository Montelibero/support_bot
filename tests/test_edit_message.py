import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
import datetime
from bot.routers.supports import router as support_router
from config.bot_config import bot_config
from tests.conftest import MOCK_SERVER_URL, TEST_BOT_TOKEN

@pytest.mark.asyncio
async def test_edit_message_flow(mock_server, repo):
    """
    Test flow:
    1. User sends a Ticket.
    2. Bot forwards it to Master (ID 1).
    3. User EDITS the Ticket.
    4. Bot sends a Notification to Master (Reply to ID 1).
    """
    
    # 1. Setup
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(MOCK_SERVER_URL)
    )
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(support_router)

    # Middleware
    # Middleware
    from aiogram import BaseMiddleware
    class MockMiddleware(BaseMiddleware):
        def __init__(self, repo_instance, config_instance, settings_instance):
            self.repo = repo_instance
            self.config = config_instance
            self.settings = settings_instance

        async def __call__(self, handler, event, data):
            data['repo'] = self.repo
            data['config'] = self.config
            data['bot_settings'] = self.settings
            return await handler(event, data)

    # Configure Bot Settings
    MASTER_CHAT_ID = -100999
    from unittest.mock import AsyncMock, MagicMock
    mock_config = MagicMock()
    mock_settings = MagicMock()
    mock_settings.master_chat = MASTER_CHAT_ID
    mock_settings.block_links = False
    mock_settings.ignore_users = []
    mock_settings.special_commands = 0
    mock_settings.use_auto_reply = False
    mock_settings.mark_bad = False
    mock_settings.master_thread = None
    
    mock_config.get_bot_setting.return_value = mock_settings

    dp.update.middleware(MockMiddleware(repo, mock_config, mock_settings))

    # 2. Step 1: User sends Ticket
    USER_ID = 777
    ORIGINAL_TEXT = "My original problem"
    MSG_ID = 50
    
    update_ticket = types.Update(
        update_id=1,
        message=types.Message(
            message_id=MSG_ID,
            date=datetime.datetime.now(),
            chat=types.Chat(id=USER_ID, type='private'),
            from_user=types.User(id=USER_ID, is_bot=False, first_name="User", username="user"),
            text=ORIGINAL_TEXT
        )
    )
    
    await dp.feed_update(bot=bot, update=update_ticket)
    
    # Verify Forward
    req_forward = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(MASTER_CHAT_ID)), None)
    assert req_forward is not None
    assert ORIGINAL_TEXT in req_forward["data"]["text"]
    
    # MockServer returns message_id=1 by default (as per our analysis)
    # The Code should have saved: MsgID 50 -> ResendID 1.
    
    mock_server.clear()

    # 3. Step 2: User EDITS Ticket
    EDITED_TEXT = "My EDITED problem"
    
    update_edit = types.Update(
        update_id=2,
        edited_message=types.Message(
            message_id=MSG_ID, # SAME ID
            date=datetime.datetime.now(),
            chat=types.Chat(id=USER_ID, type='private'),
            from_user=types.User(id=USER_ID, is_bot=False, first_name="User", username="user"),
            text=EDITED_TEXT,
            # Simulating correct edit_date (int timestamp)
            edit_date=int(datetime.datetime.now().timestamp())
        )
    )
    
    await dp.feed_update(bot=bot, update=update_edit)
    
    # Verify Notification
    # Should be a sendMessage to Master Chat
    req_notification = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(MASTER_CHAT_ID)), None)
    assert req_notification is not None, "Notification about edit was not sent"
    
    text = req_notification["data"]["text"]
    assert EDITED_TEXT in text
    assert "отредактировано" in text
    
    # Verify it is a reply
    reply_to = req_notification["data"].get("reply_to_message_id")
    assert str(reply_to) == "1", f"Expected reply to ID 1, got {reply_to}"

    await bot.session.close()

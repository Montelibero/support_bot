import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
import datetime

from bot.routers.supports import router as support_router
from config.bot_config import bot_config
from bot.middlewares.db import DbSessionMiddleware
from tests.conftest import MOCK_SERVER_URL, TEST_BOT_TOKEN

@pytest.mark.asyncio
async def test_ticket_flow(mock_server, repo):
    """
    Test flow:
    1. User sends a text message (ticket)
    2. Bot forwards (resends) message to Master Chat
    3. Admin sets their alias (/myname)
    4. Admin replies to the ticket
    5. User receives the reply
    """
    # 1. Setup Bot and Dispatcher with Middleware
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(MOCK_SERVER_URL)
    )
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(support_router)

    # Middleware to inject MockRepo
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
    MASTER_CHAT_ID = -100123456789
    
    from unittest.mock import AsyncMock, MagicMock
    mock_config = MagicMock()
    mock_settings = MagicMock()
    mock_settings.master_chat = MASTER_CHAT_ID
    mock_settings.ignore_commands = False
    mock_settings.master_thread = None
    mock_settings.block_links = False
    mock_settings.ignore_users = []
    mock_settings.special_commands = 0
    mock_settings.use_auto_reply = True
    mock_settings.auto_reply = "We received your message"
    mock_settings.mark_bad = False # Prevent reaction calls
    
    mock_config.get_bot_setting.return_value = mock_settings
    mock_config.media_groups = {} # Add media_groups

    dp.update.middleware(MockMiddleware(repo, mock_config, mock_settings))

    # 2. Simulate User Ticket
    USER_ID = 111222
    USER_CHAT_ID = 111222
    USERNAME = "test_user"
    TICKET_TEXT = "Help me please!"
    
    update_ticket = types.Update(
        update_id=1,
        message=types.Message(
            message_id=100,
            date=datetime.datetime.now(),
            chat=types.Chat(id=USER_CHAT_ID, type='private'),
            from_user=types.User(id=USER_ID, is_bot=False, first_name="Test", username=USERNAME),
            text=TICKET_TEXT
        )
    )

    await dp.feed_update(bot=bot, update=update_ticket)

    # Verify 1: Message forwarded to Master Chat
    req_master = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(MASTER_CHAT_ID)), None)
    assert req_master is not None, "Message was not forwarded to Master Chat"
    assert TICKET_TEXT in req_master["data"]["text"]
    assert f"| Test | @{USERNAME}" in req_master["data"]["text"]

    # Verify 2: Auto-reply to User
    req_user = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(USER_CHAT_ID) and "received" in r["data"]["text"]), None)
    assert req_user is not None, "Auto-reply was not sent to User"

    mock_server.clear()

    # 3. Simulate Admin Setting Alias
    ADMIN_ID = 888999
    ADMIN_ALIAS = "SupportAgent007"
    
    update_admin_name = types.Update(
        update_id=2,
        message=types.Message(
            message_id=200,
            date=datetime.datetime.now(),
            chat=types.Chat(id=MASTER_CHAT_ID, type='supergroup'),
            from_user=types.User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="admin_tg"),
            text=f"/myname {ADMIN_ALIAS}"
        )
    )
    
    await dp.feed_update(bot=bot, update=update_admin_name)
    
    # Verify Admin Name Saved confirmation
    req_admin = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(MASTER_CHAT_ID)), None)
    assert req_admin is not None
    assert f'Имя сохранено как "{ADMIN_ALIAS}"' in req_admin["data"]["text"]

    mock_server.clear()

    # 4. Simulate Admin Reply
    REPLY_TEXT = "Everything will be fine."
    
    # To reply, Admin replies to the FORWARDED message.
    # We need to know the message_id of the forwarded message in Master Chat.
    # In our mock flow, MockServer just returns {"message_id": 1, ...} for the sendMessage call.
    # But repo.save_message_ids was called with resend_id=1 (from MockServer response).
    
    # We need to construct the reply update.
    # It must have reply_to_message with message_id matching what was saved in DB.
    # MockRepo saved: message_id=100 (User Msg), resend_id=1 (Master Msg)
    
    update_admin_reply = types.Update(
        update_id=3,
        message=types.Message(
            message_id=201,
            date=datetime.datetime.now(),
            chat=types.Chat(id=MASTER_CHAT_ID, type='supergroup'),
            from_user=types.User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="admin_tg"),
            reply_to_message=types.Message(
                message_id=1, # Matches resend_id in DB
                date=datetime.datetime.now(),
                chat=types.Chat(id=MASTER_CHAT_ID, type='supergroup'),
                from_user=types.User(id=123456, is_bot=True, first_name="Test Bot", username="test_bot"), # Bot sent it
                text="Forwarded text..."
            ),
            text=REPLY_TEXT
        )
    )
    
    await dp.feed_update(bot=bot, update=update_admin_reply)
    
    # Verify User receives reply
    req_reply = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(USER_CHAT_ID)), None)
    assert req_reply is not None, "Reply was not sent to User"
    assert REPLY_TEXT in req_reply["data"]["text"]
    assert f"Вам ответил {ADMIN_ALIAS}" in req_reply["data"]["text"]

    await bot.session.close()

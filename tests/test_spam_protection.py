import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
import datetime
from aiogram.types import MessageEntity

from bot.routers.supports import router as support_router
from config.bot_config import bot_config
from tests.conftest import MOCK_SERVER_URL, TEST_BOT_TOKEN

@pytest.mark.asyncio
async def test_spam_protection(mock_server, repo):
    """
    Test flow:
    1. New User sends a message with a LINK.
    2. Bot blocks it (checks has_user_received_reply = False).
    3. User sends a TEXT message (no link).
    4. Bot forwards it.
    5. Admin replies.
    6. User sends a message with a LINK again.
    7. Bot accepts it (has_user_received_reply = True).
    """
    
    # 1. Setup
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(MOCK_SERVER_URL)
    )
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(support_router)

    # Middleware injection
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
    mock_settings.block_links = True  # Enable link blocking
    mock_settings.ignore_users = []
    mock_settings.special_commands = 0
    mock_settings.use_auto_reply = False
    mock_settings.mark_bad = False
    
    # Mock config methods if needed
    mock_config.get_bot_setting.return_value = mock_settings
    mock_config.media_groups = {} # Add media_groups for resend_message_plus

    dp.update.middleware(MockMiddleware(repo, mock_config, mock_settings))

    # 2. Test Step 1: User sends Link (Blocked)
    USER_ID = 555
    text_with_link = "Check this http://spam.com"
    
    # Entity for "http://spam.com"
    # "Check this " is 11 chars. Url starts at 11, length 15.
    entity = MessageEntity(type="url", offset=11, length=15)
    
    update_link_1 = types.Update(
        update_id=1,
        message=types.Message(
            message_id=10,
            date=datetime.datetime.now(),
            chat=types.Chat(id=USER_ID, type='private'),
            from_user=types.User(id=USER_ID, is_bot=False, first_name="Spammer", username="spam_bot"),
            text=text_with_link,
            entities=[entity]
        )
    )
    
    await dp.feed_update(bot=bot, update=update_link_1)
    
    # Verify Block Message
    req_block = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(USER_ID)), None)
    assert req_block is not None
    assert "Links and media are not allowed" in req_block["data"]["text"]
    
    mock_server.clear()

    # 3. Test Step 2: User sends Text (Allowed)
    update_text = types.Update(
        update_id=2,
        message=types.Message(
            message_id=11,
            date=datetime.datetime.now(),
            chat=types.Chat(id=USER_ID, type='private'),
            from_user=types.User(id=USER_ID, is_bot=False, first_name="Spammer", username="spam_bot"),
            text="Just normal text help me"
        )
    )
    
    await dp.feed_update(bot=bot, update=update_text)
    
    # Verify Forwarded to Master
    req_forward = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(MASTER_CHAT_ID)), None)
    assert req_forward is not None
    assert "Just normal text" in req_forward["data"]["text"]
    
    mock_server.clear()

    # 4. Step 3: Admin Replies
    # Needed to allow next links
    # We need to simulate the DB state change: "has_user_received_reply" must become True.
    # In real code, when admin replies, the message is saved to DB.
    # But "has_user_received_reply" checks if there is a message with chat_for_id == USER_ID.
    # So we need to Simulate Admin Reply Update.
    
    # MockRepo needs to know that message 11 (User Msg) was resold as ID 2 (Master Msg).
    # Since we cleared mock_server, let's assume forward got ID 2.
    await repo.save_message_ids(bot.id, USER_ID, 11, 2, USER_ID, MASTER_CHAT_ID)
    
    # Register Admin so they can reply
    ADMIN_ID = 888
    await repo.save_user_name(ADMIN_ID, "SuperAdmin", bot.id)

    # Now Admin replies to ID 2
    update_reply = types.Update(
        update_id=3,
        message=types.Message(
            message_id=200,
            date=datetime.datetime.now(),
            chat=types.Chat(id=MASTER_CHAT_ID, type='supergroup'),
            from_user=types.User(id=888, is_bot=False, first_name="Admin"),
            reply_to_message=types.Message(
                message_id=2, # Matches resend_id
                date=datetime.datetime.now(),
                chat=types.Chat(id=MASTER_CHAT_ID, type='supergroup'),
                from_user=types.User(id=123456, is_bot=True, first_name="Bot", username="bot"),
                text="Forwarded..."
            ),
            text="Reply allowed now"
        )
    )
    
    await dp.feed_update(bot=bot, update=update_reply)
    
    # Verify reply sent to user (this also means repo saved the reply info)
    req_user_reply = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(USER_ID)), None)
    assert req_user_reply is not None
    
    # CRITICAL: We need to ensure that MockRepo NOW returns True for has_user_received_reply(USER_ID).
    # has_user_received_reply checks if there is a message where chat_for_id == USER_ID.
    # The usage in code:
    # await resend_message_plus(...) -> calls repo.save_message_ids(...)
    # with chat_for_id = resend_info.chat_from_id (which is USER_ID)
    
    # Let's verify repo state
    has_reply = await repo.has_user_received_reply(bot.id, USER_ID)
    assert has_reply is True, "User should satisfy 'has received reply' condition"

    mock_server.clear()

    # 5. Step 4: User sends Link AGAIN (Should be Allowed)
    update_link_2 = types.Update(
        update_id=4,
        message=types.Message(
            message_id=12,
            date=datetime.datetime.now(),
            chat=types.Chat(id=USER_ID, type='private'),
            from_user=types.User(id=USER_ID, is_bot=False, first_name="Spammer", username="spam_bot"),
            text="Link again http://spam.com",
            entities=[MessageEntity(type="url", offset=11, length=15)]
        )
    )
    
    await dp.feed_update(bot=bot, update=update_link_2)
    
    # Verify Forwarded (NOT BLOCKED)
    req_forward_2 = next((r for r in mock_server if r["method"] == "sendMessage" and str(r["data"]["chat_id"]) == str(MASTER_CHAT_ID)), None)
    assert req_forward_2 is not None
    assert "Link again" in req_forward_2["data"]["text"]

    await bot.session.close()

import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
import datetime
import logging

from bot.routers.supports import router as support_router
from tests.conftest import TEST_BOT_TOKEN

# Custom Mock Server to simulate "Bad Request: message to be replied not found"
@pytest.fixture
async def mock_fail_server(unused_tcp_port):
    port = unused_tcp_port
    host = "localhost"
    url = f"http://{host}:{port}"
    
    routes = web.RouteTableDef()
    received_requests = []

    @routes.post("/bot{token}/getMe")
    async def get_me(request):
        return web.json_response({
            "ok": True,
            "result": {
                "id": 123456,
                "is_bot": True,
                "first_name": "Test Bot",
                "username": "test_bot",
                "can_join_groups": True,
                "can_read_all_group_messages": False,
                "supports_inline_queries": False
            }
        })

    @routes.post("/bot{token}/sendMessage")
    async def send_message(request):
        if request.content_type == 'application/json':
            data = await request.json()
        else:
            data = await request.post()
        data = dict(data)
        
        received_requests.append({"method": "sendMessage", "data": data})

        # Simulate error if reply_to_message_id is present (and it's the specific one we expect to fail)
        # For this test, we assume ANY reply in this specific test step will fail if it has reply_to_message_id
        if 'reply_to_message_id' in data:
             return web.json_response({
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message to be replied not found"
            }, status=400)

        return web.json_response({
            "ok": True,
            "result": {
                "message_id": 999,
                "date": 1234567890,
                "chat": {"id": int(data.get('chat_id', 0)), "type": "private", "first_name": "Test"},
                "text": data.get('text', '')
            }
        })

    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    yield received_requests, url

    await runner.cleanup()

@pytest.mark.asyncio
async def test_reply_to_deleted_message(mock_fail_server, repo):
    requests, server_url = mock_fail_server

    session = AiohttpSession(
        api=TelegramAPIServer.from_base(server_url)
    )
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(support_router)

    # Mock Middleware
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

    # Setup Mocks
    from unittest.mock import MagicMock
    mock_config = MagicMock()
    mock_settings = MagicMock()
    MASTER_CHAT_ID = -100123456789
    mock_settings.master_chat = MASTER_CHAT_ID
    mock_settings.ignore_commands = False
    mock_settings.master_thread = None
    mock_settings.block_links = False
    mock_settings.ignore_users = []
    mock_settings.special_commands = 0
    mock_settings.use_auto_reply = False
    mock_settings.mark_bad = False
    mock_config.get_bot_setting.return_value = mock_settings
    mock_config.media_groups = {}
    
    dp.update.middleware(MockMiddleware(repo, mock_config, mock_settings))

    # Pre-populate repo with user info so /send works
    ADMIN_ID = 888999
    USER_ID = 111222
    USER_CHAT_ID = 111222
    repo.users[ADMIN_ID] = "Admin"
    repo.users[USER_ID] = "User"

    # Simulate Admin trying to reply to a forwarded message
    # The original flow: User sends message -> Bot forwards to Master Chat -> Admin replies to Forwarded Message
    # We simulate the Admin reply step directly.
    
    # Needs to exist in DB for logic to work
    # save_message_ids(bot_id, user_id, message_id, resend_id, chat_from_id, chat_for_id)
    # message_id=100 (Original User Msg), resend_id=200 (Forwarded Msg in Master Chat)
    await repo.save_message_ids(bot.id, USER_ID, 100, 200, USER_CHAT_ID, MASTER_CHAT_ID)

    # Admin replies to message 200 in Master Chat
    update_reply = types.Update(
        update_id=1,
        message=types.Message(
            message_id=300,
            date=datetime.datetime.now(),
            chat=types.Chat(id=MASTER_CHAT_ID, type='supergroup'),
            from_user=types.User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="admin"),
            text="Reply Content",
            reply_to_message=types.Message(
                message_id=200, # pointing to the forwarded message
                date=datetime.datetime.now(),
                chat=types.Chat(id=MASTER_CHAT_ID, type='supergroup'),
                from_user=types.User(id=123456, is_bot=True, first_name="Bot", username="bot"),
                text="Forwarded Content"
            )
        )
    )

    # This should trigger `cmd_resend` -> `resend_message_plus`
    # It will try to send a message to USER_CHAT_ID with reply_to_message_id=100 (looked up from DB)
    # Our mock server is configured to fail if reply_to_message_id is present.
    await dp.feed_update(bot=bot, update=update_reply)

    # Assertions
    # 1. Check if the bot tried to send a message with reply_to_message_id
    reply_attempts = [r for r in requests if r["method"] == "sendMessage" and 'reply_to_message_id' in r["data"]]
    assert len(reply_attempts) > 0, "Bot should have attempted to reply first"

    # 2. Check if the bot eventually sent the message successfully WITHOUT reply_to_message_id (The Fix)
    # Since we haven't implemented the fix yet, we expect this valid request to be MISSING or for the code to have raised/logged an error without retrying.
    fallback_attempts = [r for r in requests if r["method"] == "sendMessage" and 'reply_to_message_id' not in r["data"] and r["data"]["chat_id"] == str(USER_CHAT_ID)]
    
    # Ideally, after the fix, this assertion should PASS. For now, we expect it to FAIL or be empty.
    if not fallback_attempts:
        pytest.fail("Bot did not retry sending the message without reply_to_message_id after receiving 'message not found' error")

    await bot.session.close()

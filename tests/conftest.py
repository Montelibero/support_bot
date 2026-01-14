import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from aiohttp import web
from aiogram import Dispatcher
from aiogram_dialog import setup_dialogs

from bot.routers.supports import router as support_router
from database.repositories import Repo

# Constants for Mock Server
MOCK_SERVER_PORT = 8081
MOCK_SERVER_HOST = "localhost"
MOCK_SERVER_URL = f"http://{MOCK_SERVER_HOST}:{MOCK_SERVER_PORT}"
TEST_BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

class MockRepo(Repo):
    def __init__(self):
        self.users = {}
        self.messages = []

    async def get_user_info(self, user_id):
        # Return a mock object if user exists
        if user_id in self.users:
            user = MagicMock()
            user.user_id = user_id
            user.user_name = self.users[user_id]
            return user
        return None
    
    async def get_all_users(self, with_username=False):
        return list(self.users.values())

    async def save_user_name(self, user_id, user_name, bot_id):
        print(f"[MockRepo] save_user_name called: {user_id} -> {user_name}")
        self.users[user_id] = user_name

    async def get_last_user_message(self, bot_id, user_id):
        # Return last message from mock storage if needed. 
        # For simple flow validation we can return None (no recent message)
        # or implement a lists of messages
        return None 

    async def save_message_ids(self, bot_id, user_id, message_id, resend_id, chat_from_id, chat_for_id):
        print(f"[MockRepo] save_message_ids: msg={message_id} resend={resend_id}")
        self.messages.append({
            "bot_id": bot_id,
            "user_id": user_id,
            "message_id": message_id,
            "resend_id": resend_id,
            "chat_from_id": chat_from_id,
            "chat_for_id": chat_for_id
        })

    async def get_message_resend_info(self, bot_id, message_id=None, resend_id=None, chat_from_id=None, chat_for_id=None):
        # Search in self.messages
        # Result should be an object with attributes accessing like .message_id
        for msg in self.messages:
            if bot_id != msg["bot_id"]: continue
            if message_id and msg["message_id"] != message_id: continue
            if resend_id and msg["resend_id"] != resend_id: continue
            if chat_from_id and msg["chat_from_id"] != chat_from_id: continue
            if chat_for_id and msg["chat_for_id"] != chat_for_id: continue
            
            # Found. enhance generic dict or create SimpleNamespace
            from types import SimpleNamespace
            return SimpleNamespace(**msg)
        return None

    async def has_user_received_reply(self, bot_id: int, user_id: int) -> bool:
        for msg in self.messages:
             if msg["bot_id"] == bot_id and msg["chat_for_id"] == user_id:
                 return True
        return False

@pytest.fixture
def repo():
    return MockRepo()

@pytest.fixture
def dp():
    dp = Dispatcher()
    dp.include_router(support_router)
    setup_dialogs(dp)
    return dp

@pytest.fixture
async def mock_server():
    """Starts a local mock Telegram server."""
    routes = web.RouteTableDef()
    received_requests = []

    @routes.post("/bot{token}/deleteWebhook")
    async def delete_webhook(request):
        received_requests.append({"method": "deleteWebhook", "token": request.match_info['token']})
        return web.json_response({"ok": True, "result": True})
        
    @routes.post("/bot{token}/setMyCommands")
    async def set_my_commands(request):
        if request.content_type == 'application/json':
            data = await request.json()
        else:
            data = await request.post()
            
        received_requests.append({"method": "setMyCommands", "token": request.match_info['token'], "data": dict(data)})
        return web.json_response({"ok": True, "result": True})

    @routes.post("/bot{token}/getMe")
    async def get_me(request):
        received_requests.append({"method": "getMe", "token": request.match_info['token']})
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
        # Debug print
        print(f"[MockServer] content_type: {request.content_type}")
        
        if request.content_type == 'application/json':
            try:
                data = await request.json()
            except:
                data = {}
        else:
            # Handle x-www-form-urlencoded or multipart/form-data
            data = await request.post()
        
        # Convert MultiDict to regular dict for easier testing
        data = dict(data)
        
        print(f"[MockServer] sendMessage data: {data}")
        
        # Cast chat_id to int if possible, as form data might be strings
        try:
            chat_id = int(data.get('chat_id', 12345))
        except (ValueError, TypeError):
            chat_id = 12345
            
        text = data.get('text', 'test_text')

        received_requests.append({"method": "sendMessage", "token": request.match_info['token'], "data": data})
        return web.json_response({
            "ok": True,
            "result": {
                "message_id": 1,
                "date": 1234567890,
                "chat": {"id": chat_id, "type": "private", "first_name": "Test"},
                "text": text
            }
        })

    @routes.post("/bot{token}/sendPhoto")
    async def send_photo(request):
        if request.content_type == 'application/json':
            data = await request.json()
        else:
            data = await request.post()
            
        data = dict(data)
        received_requests.append({"method": "sendPhoto", "token": request.match_info['token'], "data": data})
        
        try:
            chat_id = int(data.get('chat_id', 12345))
        except (ValueError, TypeError):
            chat_id = 12345
            
        return web.json_response({
            "ok": True,
            "result": {
                "message_id": 2, 
                "date": 1234567890,
                "chat": {"id": chat_id, "type": "private"},
                "photo": [] 
            }
        })

    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, MOCK_SERVER_HOST, MOCK_SERVER_PORT)
    await site.start()

    yield received_requests

    await runner.cleanup()

@pytest.fixture(autouse=True)
def cleanup_router():
    yield
    # Detach all global routers
    
    from bot.routers.supports import router as support_router
    if support_router.parent_router:
        support_router._parent_router = None

    from bot.routers.admin import router as admin_router
    if admin_router.parent_router:
        admin_router._parent_router = None

    from bot.routers.admin_dialog import dialog_all
    if dialog_all.parent_router:
        dialog_all._parent_router = None


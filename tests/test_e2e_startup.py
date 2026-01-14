import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from config.bot_config import bot_config
from main import aiogram_on_startup_polling
from tests.conftest import MOCK_SERVER_URL, TEST_BOT_TOKEN

@pytest.mark.asyncio
async def test_startup_sends_message_to_admin(mock_server):
    # Setup Bot with custom session pointing to mock server
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(MOCK_SERVER_URL)
    )
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dp = Dispatcher()

    # Mock admin ID in config
    bot_config.ADMIN_ID = 84131737

    # Execute startup logic
    await aiogram_on_startup_polling(dp, bot)

    # Verify
    # Expecting: deleteWebhook, setMyCommands, sendMessage
    assert len(mock_server) >= 3
    
    # Check if sendMessage was called correctly
    send_message_req = next((r for r in mock_server if r["method"] == "sendMessage"), None)
    assert send_message_req is not None
    # Compare as strings to be safe against form-data stringification
    assert str(send_message_req["data"]["chat_id"]) == str(bot_config.ADMIN_ID)
    assert send_message_req["data"]["text"] == "Bot started"
    
    await bot.session.close()

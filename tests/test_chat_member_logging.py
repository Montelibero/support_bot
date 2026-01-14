
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.enums import ChatMemberStatus
from bot.routers.supports import on_my_chat_member
from loguru import logger

@pytest.fixture
def capture_logs():
    logs = []
    handler_id = logger.add(lambda msg: logs.append(msg))
    yield logs
    logger.remove(handler_id)

@pytest.mark.asyncio
async def test_kicked_logging(capture_logs):
    # Mock Bot
    bot = AsyncMock()
    bot.id = 123
    user_mock = MagicMock()
    user_mock.id = 123
    user_mock.username = "test_bot"
    bot.me.return_value = user_mock

    # Mock Update
    chat = MagicMock()
    chat.id = 999
    
    old_member = MagicMock()
    old_member.status = ChatMemberStatus.MEMBER
    
    new_member = MagicMock()
    new_member.status = ChatMemberStatus.KICKED
    
    update = MagicMock()
    update.chat = chat
    update.old_chat_member = old_member
    update.new_chat_member = new_member

    # Execute
    await on_my_chat_member(update, bot)

    # Verify
    assert any("Bot 123 (@test_bot) was kicked from chat 999" in str(log) for log in capture_logs)

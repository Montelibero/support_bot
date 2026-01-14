import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message
from bot.routers.supports import cmd_add_ignore

@pytest.fixture
def bot():
    b = AsyncMock()
    b.id = 12345
    return b

@pytest.fixture
def message(bot):
    msg = AsyncMock()
    msg.bot = bot
    msg.from_user = MagicMock()
    msg.from_user.id = 888  # Admin/Owner
    msg.chat = MagicMock()
    msg.chat.id = 222
    msg.text = "/ignore"
    return msg

@pytest.fixture
def config():
    c = AsyncMock()
    return c

@pytest.fixture
def settings():
    s = MagicMock()
    s.master_chat = 222
    s.ignore_users = [101, 102, 103]
    return s

@pytest.mark.asyncio
async def test_ignore_wrong_chat(message, bot, settings, config):
    settings.master_chat = 999
    message.chat.id = 222 # Different from master_chat
    
    await cmd_add_ignore(message, bot, settings, config)
    
    # Should perform no action
    message.answer.assert_not_called()
    config.update_bot_setting.assert_not_called()


@pytest.mark.asyncio
async def test_ignore_list_short(message, bot, settings, config):
    # Setup: ignore_users has 3 items
    settings.ignore_users = [101, 102, 103]
    message.text = "/ignore" 
    
    await cmd_add_ignore(message, bot, settings, config)
    
    message.answer.assert_called_once()
    text = message.answer.call_args.kwargs.get('text') or message.answer.call_args[0][0]
         
    assert "Всего в игноре: 3" in text
    assert "Последние 5: 101, 102, 103" in text


@pytest.mark.asyncio
async def test_ignore_list_long(message, bot, settings, config):
    # Setup: ignore_users has 6 items
    settings.ignore_users = [1, 2, 3, 4, 5, 6]
    message.text = "/ignore" 
    
    await cmd_add_ignore(message, bot, settings, config)
    
    text = message.answer.call_args.kwargs.get('text') or message.answer.call_args[0][0]
    
    # Should show last 5: 2, 3, 4, 5, 6
    assert "Всего в игноре: 6" in text
    assert "Последние 5: 2, 3, 4, 5, 6" in text
    assert "1," not in text 

@pytest.mark.asyncio
async def test_ignore_add_user(message, bot, settings, config):
    settings.ignore_users = [100]
    message.text = "/ignore 200"
    
    await cmd_add_ignore(message, bot, settings, config)
    
    assert 200 in settings.ignore_users
    config.update_bot_setting.assert_called_once_with(settings)
    message.answer.assert_called()
    assert "добавлен" in (message.answer.call_args.kwargs.get('text') or message.answer.call_args[0][0])

@pytest.mark.asyncio
async def test_ignore_remove_user(message, bot, settings, config):
    settings.ignore_users = [100, 200]
    message.text = "/ignore 200"
    
    await cmd_add_ignore(message, bot, settings, config)
    
    assert 200 not in settings.ignore_users
    config.update_bot_setting.assert_called_once_with(settings)
    message.answer.assert_called()
    assert "удален" in (message.answer.call_args.kwargs.get('text') or message.answer.call_args[0][0])

@pytest.mark.asyncio
async def test_ignore_invalid_id(message, bot, settings, config):
    message.text = "/ignore abc"
    
    await cmd_add_ignore(message, bot, settings, config)
    
    config.update_bot_setting.assert_not_called()
    assert "ID пользователя должен быть числом" in (message.answer.call_args.kwargs.get('text') or message.answer.call_args[0][0])

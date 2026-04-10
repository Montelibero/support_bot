import pytest
from unittest.mock import AsyncMock, MagicMock
from bot.routers.supports import cmd_myname


@pytest.fixture
def bot():
    b = AsyncMock()
    b.id = 12345
    return b


@pytest.fixture
def config():
    from unittest.mock import AsyncMock

    c = MagicMock()
    c.update_bot_setting = AsyncMock()
    return c


@pytest.fixture
def message(bot):
    msg = AsyncMock()
    msg.bot = bot
    # Mock data attributes with MagicMock/simple objects, not AsyncMock,
    # because they are not awaited (e.g. message.from_user.id)
    msg.from_user = MagicMock()
    msg.from_user.id = 111
    msg.chat = MagicMock()
    msg.chat.id = 222
    msg.text = "/myname Alex"
    return msg


@pytest.mark.asyncio
async def test_cmd_myname_success(message, bot, repo, config):
    # Setup
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = False
    settings.local_names = {}

    # Execute
    await cmd_myname(message, bot, repo, settings, config)

    # Verify
    assert 111 in repo.users
    assert repo.users[111] == "Alex"
    message.answer.assert_called_with(text='Имя сохранено как "Alex" (глобально)')


@pytest.mark.asyncio
async def test_cmd_myname_duplicate(message, bot, repo, config):
    # Setup
    repo.users[999] = "Alex"  # Name already taken

    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = False
    settings.local_names = {}

    # Execute
    await cmd_myname(message, bot, repo, settings, config)

    # Verify
    # Should not overwrite or add new
    assert 111 not in repo.users
    message.answer.assert_called_with(text="Псевдоним Alex уже занят")

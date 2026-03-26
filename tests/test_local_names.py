import pytest
from unittest.mock import MagicMock, AsyncMock

from bot.routers.supports import _resolve_agent_name, _no_name_error_text
from bot.routers.supports import cmd_myname, cmd_show_names, cmd_resend
from config.bot_config import BotConfig


@pytest.fixture
def local_settings():
    s = MagicMock()
    s.use_local_names = True
    s.local_names = {"111": "Алексей"}
    return s


@pytest.fixture
def global_settings():
    s = MagicMock()
    s.use_local_names = False
    s.local_names = {}
    return s


def test_resolve_local_name_found(local_settings):
    assert _resolve_agent_name(111, local_settings, user_info=None) == "Алексей"


def test_resolve_local_name_missing(local_settings):
    assert _resolve_agent_name(999, local_settings, user_info=None) is None


def test_resolve_global_name_found(global_settings):
    user_info = MagicMock()
    user_info.user_name = "GlobalName"
    assert _resolve_agent_name(111, global_settings, user_info=user_info) == "GlobalName"


def test_resolve_global_name_missing(global_settings):
    assert _resolve_agent_name(111, global_settings, user_info=None) is None


def test_local_mode_ignores_global_user_info(local_settings):
    user_info = MagicMock()
    user_info.user_name = "GlobalName"
    # user_id 999 not in local_names → None even though user_info exists
    assert _resolve_agent_name(999, local_settings, user_info=user_info) is None


def test_error_text_local_mode():
    text = _no_name_error_text(use_local_names=True)
    assert "локальные имена" in text.lower()
    assert "/myname" in text


def test_error_text_global_mode():
    text = _no_name_error_text(use_local_names=False)
    assert "глобальные имена" in text.lower()
    assert "/myname" in text


@pytest.fixture
def bot():
    b = AsyncMock()
    b.id = 12345
    return b


@pytest.fixture
def repo():
    from tests.conftest import MockRepo
    return MockRepo()


@pytest.fixture
def config():
    c = MagicMock(spec=BotConfig)
    c.update_bot_setting = AsyncMock()
    return c


def _make_message(text, chat_id, user_id=111):
    msg = AsyncMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    return msg


@pytest.mark.asyncio
async def test_myname_local_saves_to_settings(bot, repo, config):
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {}

    msg = _make_message("/myname Локальный", 222)
    await cmd_myname(msg, bot, repo, settings, config)

    assert settings.local_names[str(msg.from_user.id)] == "Локальный"
    config.update_bot_setting.assert_awaited_once_with(settings)
    assert "локально" in msg.answer.call_args[1]["text"].lower()


@pytest.mark.asyncio
async def test_myname_local_duplicate_rejected(bot, repo, config):
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {"999": "Занято"}

    msg = _make_message("/myname Занято", 222)
    await cmd_myname(msg, bot, repo, settings, config)

    assert str(msg.from_user.id) not in settings.local_names
    assert "занят" in msg.answer.call_args[1]["text"].lower()


@pytest.mark.asyncio
async def test_myname_global_shows_label(bot, repo, config):
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = False
    settings.local_names = {}

    msg = _make_message("/myname Глобал", 222)
    await cmd_myname(msg, bot, repo, settings, config)

    assert 111 in repo.users
    assert "глобально" in msg.answer.call_args[1]["text"].lower()


@pytest.mark.asyncio
async def test_myname_local_allows_globally_existing_name(bot, repo, config):
    """Name exists globally but not locally → allowed in local mode."""
    repo.users[999] = "Занято"  # global name exists
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {}

    msg = _make_message("/myname Занято", 222)
    await cmd_myname(msg, bot, repo, settings, config)

    assert settings.local_names[str(msg.from_user.id)] == "Занято"
    assert "локально" in msg.answer.call_args[1]["text"].lower()


@pytest.mark.asyncio
async def test_show_names_local(bot, repo):
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {"111": "Алексей", "222": "Мария"}

    msg = _make_message("/show_names", 222)
    await cmd_show_names(msg, bot, repo, settings)

    text = msg.answer.call_args[1]["text"]
    assert "Локальные имена" in text
    assert "Алексей" in text
    assert "Мария" in text
    assert "#ID" in text


@pytest.mark.asyncio
async def test_show_names_global(bot, repo):
    repo.users = {111: "GlobalAlex"}
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = False

    msg = _make_message("/show_names", 222)
    await cmd_show_names(msg, bot, repo, settings)

    text = msg.answer.call_args[1]["text"]
    assert "Глобальные имена" in text
    assert "GlobalAlex" in text


@pytest.mark.asyncio
async def test_resend_local_name_resolves(bot, repo, config):
    """When use_local_names=True and local name exists, reply is sent."""
    settings = MagicMock()
    settings.master_chat = -100
    settings.master_thread = None
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {"111": "Локал"}
    settings.mark_bad = False
    settings.use_auto_reply = False
    settings.block_links = False
    settings.ignore_users = []

    reply_msg = MagicMock()
    reply_msg.from_user = MagicMock()
    reply_msg.from_user.id = bot.id

    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = -100
    msg.chat.type = "supergroup"
    msg.from_user = MagicMock()
    msg.from_user.id = 111
    msg.reply_to_message = reply_msg
    msg.html_text = "Ваш ответ"
    msg.text = "Ваш ответ"
    msg.photo = None
    msg.document = None
    msg.sticker = None
    msg.audio = None
    msg.video = None
    msg.voice = None
    msg.video_note = None
    msg.animation = None
    msg.location = None
    msg.contact = None
    msg.venue = None
    msg.media_group_id = None

    repo.messages.append({
        "bot_id": bot.id,
        "user_id": None,
        "message_id": 50,
        "resend_id": reply_msg.message_id,
        "chat_from_id": 999,
        "chat_for_id": -100,
    })

    await cmd_resend(msg, bot, repo, settings, config)

    assert not msg.reply.called or "псевдоним" not in msg.reply.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_resend_global_name_missing_error(bot, repo, config):
    """When use_local_names=False and no global name → error mentioning global mode."""
    settings = MagicMock()
    settings.master_chat = -100
    settings.master_thread = None
    settings.ignore_commands = False
    settings.use_local_names = False
    settings.local_names = {}
    settings.mark_bad = False

    reply_msg = MagicMock()
    reply_msg.from_user = MagicMock()
    reply_msg.from_user.id = bot.id

    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = -100
    msg.from_user = MagicMock()
    msg.from_user.id = 111
    msg.reply_to_message = reply_msg

    repo.messages.append({
        "bot_id": bot.id,
        "user_id": None,
        "message_id": 50,
        "resend_id": reply_msg.message_id,
        "chat_from_id": 999,
        "chat_for_id": -100,
    })

    await cmd_resend(msg, bot, repo, settings, config)

    msg.reply.assert_called_once()
    error_text = msg.reply.call_args[0][0]
    assert "глобальные имена" in error_text.lower()
    assert "/myname" in error_text


@pytest.mark.asyncio
async def test_resend_local_name_missing_error(bot, repo, config):
    """When use_local_names=True but no local name → error mentioning local mode."""
    settings = MagicMock()
    settings.master_chat = -100
    settings.master_thread = None
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {}
    settings.mark_bad = False

    reply_msg = MagicMock()
    reply_msg.from_user = MagicMock()
    reply_msg.from_user.id = bot.id

    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = -100
    msg.from_user = MagicMock()
    msg.from_user.id = 111
    msg.reply_to_message = reply_msg

    repo.messages.append({
        "bot_id": bot.id,
        "user_id": None,
        "message_id": 50,
        "resend_id": reply_msg.message_id,
        "chat_from_id": 999,
        "chat_for_id": -100,
    })

    await cmd_resend(msg, bot, repo, settings, config)

    msg.reply.assert_called_once()
    error_text = msg.reply.call_args[0][0]
    assert "локальные имена" in error_text.lower()
    assert "/myname" in error_text

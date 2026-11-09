"""/help in the attached master chat, with plugin-extensible help text."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import BaseMiddleware, Bot, Dispatcher, types

from bot.customizations.default import DefaultBotCustomization
from bot.customizations.helper import HELPER_BOT_ID
from bot.routers.supports import CORE_HELP_COMMANDS, cmd_help

MASTER_CHAT_ID = -1001234567890


def _make_bot(bot_id: int = 999) -> AsyncMock:
    bot = AsyncMock(spec=Bot)
    bot.id = bot_id
    return bot


def _make_settings(
    *,
    bot_id: int = 999,
    master_chat: int | None = MASTER_CHAT_ID,
    ignore_commands: bool = False,
) -> MagicMock:
    settings = MagicMock()
    settings.id = bot_id
    settings.username = "support_bot"
    settings.master_chat = master_chat
    settings.ignore_commands = ignore_commands
    return settings


def _make_message(
    text: str, *, chat_id: int = MASTER_CHAT_ID, bot: AsyncMock | None = None
) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.chat.id = chat_id
    message.bot = bot or _make_bot()
    message.answer = AsyncMock()
    return message


def _make_update(
    update_id: int,
    *,
    bot: AsyncMock,
    text: str,
    chat_id: int,
    chat_type: str,
) -> types.Update:
    chat: dict = {"id": chat_id, "type": chat_type}
    if chat_type == "private":
        chat["first_name"] = "User"
    else:
        chat["title"] = "Master"
    return types.Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "from": {"id": 888, "is_bot": False, "first_name": "Agent"},
                "chat": chat,
                "date": 1700000000,
                "text": text,
            },
        },
        context={"bot": bot},
    )


class _CoreMiddleware(BaseMiddleware):
    """Supplies the dependencies production middlewares inject."""

    def __init__(self, repo, settings):
        self.repo = repo
        self.settings = settings

    async def __call__(self, handler, event, data):
        data["repo"] = self.repo
        data["config"] = MagicMock()
        data["bot_settings"] = self.settings
        return await handler(event, data)


def _sent_methods(bot: AsyncMock) -> list:
    return [call.args[0] for call in bot.await_args_list if call.args]


@pytest.mark.asyncio
async def test_help_lists_core_commands_in_master_chat():
    bot = _make_bot()
    message = _make_message("/help", bot=bot)

    await cmd_help(message, bot, _make_settings())

    text = message.answer.await_args.args[0]
    assert "<b>Команды поддержки</b>" in text
    for command, _ in CORE_HELP_COMMANDS:
        assert command in text
    # Master-chat help must not leak the helper plugin section for other bots.
    assert "/tickets" not in text


@pytest.mark.asyncio
async def test_help_appends_helper_plugin_section():
    bot = _make_bot(HELPER_BOT_ID)
    message = _make_message("/help", bot=bot)

    await cmd_help(message, bot, _make_settings(bot_id=HELPER_BOT_ID))

    text = message.answer.await_args.args[0]
    assert "<b>Helper</b>" in text
    assert "/take" in text
    assert "/tickets" in text
    assert "/ticket_close_&lt;id&gt;" in text


@pytest.mark.asyncio
async def test_help_is_silent_outside_master_chat():
    message = _make_message("/help", chat_id=777)

    await cmd_help(message, _make_bot(), _make_settings())

    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_help_works_when_ignore_commands_enabled():
    bot = _make_bot()
    message = _make_message("/help", bot=bot)

    await cmd_help(message, bot, _make_settings(ignore_commands=True))

    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_broken_plugin_help_falls_back_to_core(monkeypatch):
    class BrokenCustomization:
        async def get_help_text(self, bot_settings):
            raise RuntimeError("plugin is broken")

    monkeypatch.setattr(
        "bot.routers.supports.get_customization",
        lambda bot_id: BrokenCustomization(),
    )
    bot = _make_bot()
    message = _make_message("/help", bot=bot)

    await cmd_help(message, bot, _make_settings())

    text = message.answer.await_args.args[0]
    assert "<b>Команды поддержки</b>" in text
    assert "/help" in text


@pytest.mark.asyncio
async def test_long_plugin_help_is_chunked(monkeypatch):
    class LongCustomization:
        async def get_help_text(self, bot_settings):
            lines = "\n".join(
                f"/plugin_command_{index} — plugin help line number {index}"
                for index in range(300)
            )
            return f"\n\n<b>Plugin</b>\n{lines}"

    monkeypatch.setattr(
        "bot.routers.supports.get_customization",
        lambda bot_id: LongCustomization(),
    )
    bot = _make_bot()
    message = _make_message("/help", bot=bot)

    await cmd_help(message, bot, _make_settings())

    assert message.answer.await_count > 1
    for call in message.answer.await_args_list:
        assert len(call.args[0]) <= 4096


@pytest.mark.asyncio
async def test_default_customization_has_no_help_text():
    assert await DefaultBotCustomization().get_help_text(_make_settings()) == ""


@pytest.mark.asyncio
async def test_dispatcher_help_in_master_chat_is_answered(repo):
    from bot.routers.supports import router as support_router

    settings = _make_settings()
    bot = _make_bot()
    dispatcher = Dispatcher()
    dispatcher.include_router(support_router)
    dispatcher.update.middleware(_CoreMiddleware(repo, settings))

    await dispatcher.feed_update(
        bot=bot,
        update=_make_update(
            1,
            bot=bot,
            text="/help",
            chat_id=MASTER_CHAT_ID,
            chat_type="supergroup",
        ),
    )

    sent = _sent_methods(bot)
    assert sent
    assert "Команды поддержки" in sent[0].text


@pytest.mark.asyncio
async def test_dispatcher_help_in_private_chat_is_not_forwarded(repo):
    from bot.routers.supports import router as support_router

    settings = _make_settings()
    bot = _make_bot()
    dispatcher = Dispatcher()
    dispatcher.include_router(support_router)
    dispatcher.update.middleware(_CoreMiddleware(repo, settings))

    await dispatcher.feed_update(
        bot=bot,
        update=_make_update(
            2,
            bot=bot,
            text="/help",
            chat_id=777,
            chat_type="private",
        ),
    )

    assert not _sent_methods(bot)
    assert repo.messages == []

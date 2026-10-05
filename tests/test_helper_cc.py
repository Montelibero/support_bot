"""Helper plugin CC-assignee: Redis-backed ticket assignments.

Per docs/exec-plans/active/helper-cc-assignee.md: the taken click stores the
assignment, CC is rendered from it, the take button is suppressed while it
exists, close removes it, /tickets and /ticket_close_{id} manage it, and
router-level filters keep the plugin silent on other bots and other chats.
"""

import asyncio
import contextlib
import fnmatch
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, BaseMiddleware, Dispatcher, types

from bot.customizations import helper as helper_mod
from bot.customizations.helper import (
    HELPER_BOT_ID,
    HELPER_EVENTS_CHAT_ID,
    EndCallbackData,
    GetCallbackData,
    HelperCustomization,
)

MASTER_CHAT_ID = -1001234567890


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.scan_calls = 0

    async def get(self, key):
        return self.store.get(key)

    async def mget(self, keys):
        return [self.store.get(key) for key in keys]

    async def set(self, key, value, nx=False):
        if nx and key in self.store:
            return None  # SET NX refuses to overwrite an existing key
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)

    async def scan_iter(self, match, count=None):
        self.scan_calls += 1
        for key in list(self.store):
            if fnmatch.fnmatch(key, match):
                yield key


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(helper_mod, "_get_redis", lambda: fake)
    return fake


@pytest.fixture
def customization():
    return HelperCustomization()


def _make_bot(bot_id=HELPER_BOT_ID):
    bot = AsyncMock(spec=Bot)
    bot.id = bot_id
    return bot


def _make_settings():
    settings = MagicMock()
    settings.id = HELPER_BOT_ID
    settings.username = "mtl_helper_bot"
    settings.master_chat = MASTER_CHAT_ID
    settings.use_auto_reply = False
    settings.auto_reply = "auto"
    settings.use_local_names = False
    settings.local_names = {}
    settings.block_links = False
    settings.ignore_cjk_messages = False
    settings.ignore_users = []
    settings.mark_bad = False
    return settings


def _make_message_mock(text=None, bot=None, chat_id=MASTER_CHAT_ID):
    message = MagicMock()
    message.chat.id = chat_id
    message.bot = bot if bot is not None else _make_bot()
    message.text = text
    message.answer = AsyncMock()
    return message


def _ticket_message_mock(message_id=556):
    message = MagicMock()
    message.chat.id = MASTER_CHAT_ID
    message.message_thread_id = None
    message.message_id = message_id
    message.get_url = MagicMock(return_value="https://example.com/task/1")
    return message


def _make_update(update_id: int, payload: dict, bot) -> types.Update:
    return types.Update.model_validate(
        {"update_id": update_id, **payload}, context={"bot": bot}
    )


@pytest.mark.asyncio
async def test_taken_click_stores_assignment(customization, fake_redis):
    bot = _make_bot()
    callback = AsyncMock()
    callback.message = _ticket_message_mock()
    callback.bot = bot
    callback.from_user.username = "agent1"

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    key = f"helper:assign:{bot.id}:777"
    assert key in fake_redis.store
    assert json.loads(fake_redis.store[key]) == {"agent": "agent1", "ticket_msg": 556}


@pytest.mark.asyncio
async def test_close_click_deletes_assignment(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    callback = AsyncMock()
    callback.message = _ticket_message_mock()
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent1"

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent1")
    )

    assert f"helper:assign:{bot.id}:777" not in fake_redis.store


@pytest.mark.asyncio
async def test_close_by_other_agent_keeps_assignment(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock()
    callback.bot = bot
    callback.from_user.id = 999

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent1")
    )

    assert key in fake_redis.store


@pytest.mark.asyncio
async def test_extra_text_appends_cc_when_assigned(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot\nCC @agent1"


@pytest.mark.asyncio
async def test_extra_text_without_assignment_has_no_cc(customization, fake_redis):
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = _make_bot()

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot"


@pytest.mark.asyncio
async def test_reply_markup_suppressed_while_assigned(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    markup = await customization.get_reply_markup(user, message, _make_settings())

    assert markup is None


@pytest.mark.asyncio
async def test_reply_markup_shown_when_no_assignment(customization, fake_redis):
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = _make_bot()

    markup = await customization.get_reply_markup(user, message, _make_settings())

    assert markup is not None
    assert markup.inline_keyboard[0][0].text == "Взять"


@pytest.mark.asyncio
async def test_tickets_lists_assignments(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    message = _make_message_mock(text="/tickets", bot=bot)

    await customization.cmd_tickets(message, _make_settings())

    text = message.answer.await_args.args[0]
    assert "#ID777" in text
    assert "@agent1" in text
    assert 'href="https://t.me/c/1234567890/556"' in text
    assert "/ticket_close_556" in text


@pytest.mark.asyncio
async def test_tickets_sorted_oldest_first(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    fake_redis.store[f"helper:assign:{bot.id}:778"] = json.dumps(
        {"agent": "agent2", "ticket_msg": 100}
    )
    message = _make_message_mock(text="/tickets", bot=bot)

    await customization.cmd_tickets(message, _make_settings())

    text = message.answer.await_args.args[0]
    assert text.index("ticket_close_100") < text.index("ticket_close_556")


@pytest.mark.asyncio
async def test_tickets_empty_state(customization, fake_redis):
    message = _make_message_mock(text="/tickets")

    await customization.cmd_tickets(message, _make_settings())

    assert "нет" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_ticket_close_deletes_matching_assignment(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings())

    assert key not in fake_redis.store
    assert "закрыт" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_ticket_close_unknown_id_answered(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_999", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings())

    assert key in fake_redis.store
    assert "не найден" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_ticket_close_own_mention_suffix_accepted(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556@mtl_helper_bot", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings())

    assert key not in fake_redis.store


@pytest.mark.asyncio
async def test_ticket_close_foreign_mention_not_handled_by_hook(
    customization, fake_redis
):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556@some_other_bot", bot=bot)

    handled = await customization.handle_master_message(message, _make_settings())

    assert handled is False
    assert key in fake_redis.store


class _CoreMiddleware(BaseMiddleware):
    """Mimics the production middlewares for the full support_router tree."""

    def __init__(self, repo, settings):
        self.repo = repo
        self.settings = settings

    async def __call__(self, handler, event, data):
        data["repo"] = self.repo
        data["config"] = MagicMock()
        data["bot_settings"] = self.settings
        return await handler(event, data)


def _master_chat_update(update_id: int, bot, text: str) -> types.Update:
    return _make_update(
        update_id,
        {
            "message": {
                "message_id": 1,
                "from": {"id": 888, "is_bot": False, "first_name": "Agent"},
                "chat": {
                    "id": MASTER_CHAT_ID,
                    "type": "supergroup",
                    "title": "M",
                },
                "date": 1700000000,
                "text": text,
            }
        },
        bot,
    )


def _sent_methods(bot) -> list:
    return [call.args[0] for call in bot.await_args_list if call.args]


@pytest.mark.asyncio
async def test_core_tree_routes_tickets_command_and_user_messages(fake_redis, repo):
    """Regression for the agy review P1-1: /tickets must survive the real
    dispatcher tree (support_router -> hook), and ordinary user messages
    must still be forwarded by cmd_resend."""
    from bot.routers.supports import router as support_router

    settings = _make_settings()
    settings.use_local_names = False
    dp = Dispatcher()
    dp.include_router(support_router)
    dp.update.middleware(_CoreMiddleware(repo, settings))
    bot = _make_bot()

    # 1) /tickets in the master chat is handled by the plugin hook
    await dp.feed_update(bot=bot, update=_master_chat_update(1, bot, "/tickets"))
    sent = _sent_methods(bot)
    assert sent, "plugin hook did not answer /tickets"
    assert str(sent[0].chat_id) == str(MASTER_CHAT_ID)
    assert "Открытых тикетов нет" in sent[0].text

    # 2) a plain user message still reaches cmd_resend and is forwarded
    bot.reset_mock()
    user_update = _make_update(
        2,
        {
            "message": {
                "message_id": 2,
                "from": {
                    "id": 777,
                    "is_bot": False,
                    "first_name": "Client",
                    "username": "client",
                },
                "chat": {"id": 777, "type": "private"},
                "date": 1700000000,
                "text": "help please",
            }
        },
        bot,
    )
    await dp.feed_update(bot=bot, update=user_update)

    forwarded = [
        call.kwargs
        for call in bot.send_message.await_args_list
        if str(call.kwargs.get("chat_id")) == str(MASTER_CHAT_ID)
    ]
    assert forwarded, "user ticket was not forwarded to the master chat"
    assert "help please" in forwarded[0]["text"]


@pytest.mark.asyncio
async def test_hook_failure_falls_back_to_core_handling(fake_redis, repo):
    """A crashing plugin hook must not kill the message: the core reply
    flow still runs (agy review isolation requirement)."""
    from bot.routers.supports import router as support_router

    settings = _make_settings()
    settings.use_local_names = True
    settings.local_names = {"888": "Agent"}
    dp = Dispatcher()
    dp.include_router(support_router)
    dp.update.middleware(_CoreMiddleware(repo, settings))
    bot = _make_bot()
    await repo.save_message_ids(
        bot.id,
        777,
        message_id=50,
        resend_id=200,
        chat_from_id=777,
        chat_for_id=MASTER_CHAT_ID,
    )
    # agent reply to the bot's own message in the master chat
    update = _make_update(
        1,
        {
            "message": {
                "message_id": 3,
                "from": {"id": 888, "is_bot": False, "first_name": "Agent"},
                "chat": {
                    "id": MASTER_CHAT_ID,
                    "type": "supergroup",
                    "title": "M",
                },
                "date": 1700000000,
                "text": "your answer",
                "reply_to_message": {
                    "message_id": 200,
                    "from": {
                        "id": HELPER_BOT_ID,
                        "is_bot": True,
                        "first_name": "Helper",
                    },
                    "chat": {
                        "id": MASTER_CHAT_ID,
                        "type": "supergroup",
                        "title": "M",
                    },
                    "date": 1699999000,
                    "text": "original ticket",
                },
            }
        },
        bot,
    )

    with patch.object(
        helper_mod.HelperCustomization,
        "handle_master_message",
        new=AsyncMock(side_effect=RuntimeError("plugin exploded")),
    ):
        await dp.feed_update(bot=bot, update=update)

    sent_to_user = [
        call.kwargs
        for call in bot.send_message.await_args_list
        if call.kwargs.get("chat_id") == 777
    ]
    assert sent_to_user, "core reply flow did not run after hook failure"
    assert "Вам ответил Agent" in sent_to_user[0]["text"]


def _channel_post_update(bot, chat_id: int) -> types.Update:
    return _make_update(
        2,
        {
            "channel_post": {
                "message_id": 5,
                "chat": {"id": chat_id, "type": "channel"},
                "date": 1700000000,
                "text": "#helper command=ack op=taken url=u1 status=ok",
            }
        },
        bot,
    )


@pytest.mark.asyncio
async def test_helper_ack_processed_from_events_channel(fake_redis):
    instance = HelperCustomization()
    bot = _make_bot()
    instance._register_pending_ack(
        op="taken",
        url="u1",
        master_chat_id=MASTER_CHAT_ID,
        master_thread_id=None,
        agent_username="agent1",
        bot=bot,
    )
    dp = Dispatcher()
    dp.include_router(instance.router)
    update = _channel_post_update(bot, HELPER_EVENTS_CHAT_ID)

    await dp.feed_update(bot=bot, update=update)
    await asyncio.sleep(0)

    assert not instance._pending_acks


@pytest.mark.asyncio
async def test_helper_ack_ignored_from_other_chat(fake_redis):
    instance = HelperCustomization()
    bot = _make_bot()
    instance._register_pending_ack(
        op="taken",
        url="u1",
        master_chat_id=MASTER_CHAT_ID,
        master_thread_id=None,
        agent_username="agent1",
        bot=bot,
    )
    dp = Dispatcher()
    dp.include_router(instance.router)
    update = _channel_post_update(bot, chat_id=-100777)

    try:
        await dp.feed_update(bot=bot, update=update)

        assert ("taken", "u1") in instance._pending_acks
    finally:
        task = instance._pending_tasks.pop(("taken", "u1"), None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_helper_ack_ignored_from_other_bot(fake_redis):
    instance = HelperCustomization()
    bot = _make_bot(bot_id=999)
    instance._register_pending_ack(
        op="taken",
        url="u1",
        master_chat_id=MASTER_CHAT_ID,
        master_thread_id=None,
        agent_username="agent1",
        bot=bot,
    )
    dp = Dispatcher()
    dp.include_router(instance.router)
    update = _channel_post_update(bot, HELPER_EVENTS_CHAT_ID)

    try:
        await dp.feed_update(bot=bot, update=update)

        assert ("taken", "u1") in instance._pending_acks
    finally:
        task = instance._pending_tasks.pop(("taken", "u1"), None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_extra_text_degrades_on_redis_outage(customization, monkeypatch):
    class BrokenRedis:
        async def get(self, key):
            raise ConnectionError("redis down")

    monkeypatch.setattr(helper_mod, "_get_redis", lambda: BrokenRedis())
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = _make_bot()

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot"


# --- findings from the agy review ---


@pytest.mark.asyncio
async def test_take_without_username_refused(customization, fake_redis):
    bot = _make_bot()
    callback = AsyncMock()
    callback.message = _ticket_message_mock()
    callback.bot = bot
    callback.from_user.username = None

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    assert not fake_redis.store  # nothing stored
    bot.send_message.assert_not_awaited()  # no taken event sent
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_extra_text_survives_corrupted_assignment(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = "not a json"
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot"


@pytest.mark.asyncio
async def test_tickets_skips_corrupted_key_and_lists_good_ones(
    customization, fake_redis
):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = "not a json"
    fake_redis.store[f"helper:assign:{bot.id}:778"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    message = _make_message_mock(text="/tickets", bot=bot)

    await customization.cmd_tickets(message, _make_settings())

    text = message.answer.await_args.args[0]
    assert "#ID778" in text
    assert "#ID777" not in text


@pytest.mark.asyncio
async def test_reply_markup_degrades_to_button_on_redis_outage(
    customization, monkeypatch
):
    class BrokenRedis:
        async def get(self, key):
            raise ConnectionError("redis down")

    monkeypatch.setattr(helper_mod, "_get_redis", lambda: BrokenRedis())
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = _make_bot()

    markup = await customization.get_reply_markup(user, message, _make_settings())

    assert markup is not None  # degrades to the plain take button, no crash


@pytest.mark.asyncio
async def test_callback_queries_are_scoped_to_helper_bot(fake_redis):
    instance = HelperCustomization()
    dp = Dispatcher()
    dp.include_router(instance.router)
    bot = _make_bot(bot_id=999)
    packed = GetCallbackData(user_id=777, username="client").pack()
    update = _make_update(
        3,
        {
            "callback_query": {
                "id": "42",
                "from": {"id": 888, "is_bot": False, "first_name": "Agent"},
                "chat_instance": "ci",
                "data": packed,
            }
        },
        bot,
    )

    await dp.feed_update(bot=bot, update=update)

    assert not fake_redis.store  # handler never ran


@pytest.mark.asyncio
async def test_tickets_chunks_long_lists(customization, fake_redis):
    bot = _make_bot()
    for index in range(70):
        fake_redis.store[f"helper:assign:{bot.id}:{1000 + index}"] = json.dumps(
            {
                "agent": "agent_with_a_long_name",
                "ticket_msg": 100000 + index,
            }
        )
    message = _make_message_mock(text="/tickets", bot=bot)

    await customization.cmd_tickets(message, _make_settings())

    assert message.answer.await_count >= 2
    for call in message.answer.await_args_list:
        assert len(call.args[0]) <= 4096


@pytest.mark.asyncio
async def test_tickets_own_mention_suffix_accepted(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    message = _make_message_mock(text="/tickets@mtl_helper_bot", bot=bot)

    await customization.cmd_tickets(message, _make_settings())

    text = message.answer.await_args.args[0]
    assert "#ID777" in text


@pytest.mark.asyncio
async def test_tickets_foreign_mention_not_handled_by_hook(customization, fake_redis):
    message = _make_message_mock(text="/tickets@some_other_bot")

    handled = await customization.handle_master_message(message, _make_settings())

    assert handled is False
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_master_message_ignores_regular_text(customization, fake_redis):
    message = _make_message_mock(text="обычный текст агента")

    handled = await customization.handle_master_message(message, _make_settings())

    assert handled is False


@pytest.mark.asyncio
async def test_default_customization_never_handles_master_message():
    from bot.customizations.default import DefaultBotCustomization
    from bot.customizations.registry import get_customization

    # bots without a customization get the default one — the hook is a no-op
    assert (
        await DefaultBotCustomization().handle_master_message(
            MagicMock(), _make_settings()
        )
        is False
    )
    assert isinstance(get_customization(999), DefaultBotCustomization)


@pytest.mark.asyncio
async def test_ticket_close_delete_failure_degrades(monkeypatch):
    class DeleteBrokenRedis(FakeRedis):
        async def delete(self, key):
            raise ConnectionError("redis down on delete")

    broken = DeleteBrokenRedis()
    monkeypatch.setattr(helper_mod, "_get_redis", lambda: broken)
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    broken.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await HelperCustomization().cmd_ticket_close(message, _make_settings())

    assert key in broken.store  # delete failed, assignment kept
    assert "редис недоступен" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_tickets_escapes_agent_html(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "<b>evil&", "ticket_msg": 556}
    )
    message = _make_message_mock(text="/tickets", bot=bot)

    await customization.cmd_tickets(message, _make_settings())

    text = message.answer.await_args.args[0]
    assert "&lt;b&gt;evil&amp;" in text
    assert "<b>evil" not in text


# --- findings from the agy review, round 3 ---


@pytest.mark.asyncio
async def test_extra_text_escapes_agent_html(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "<b>test&", "ticket_msg": 556}
    )
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert "&lt;b&gt;test&amp;" in result
    assert "<b>test" not in result


@pytest.mark.asyncio
async def test_handle_master_message_ignores_keyboard_case(customization, fake_redis):
    message = _make_message_mock(text="/TICKETS")

    handled = await customization.handle_master_message(message, _make_settings())

    assert handled is True
    assert "Открытых тикетов нет" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_close_stale_button_keeps_newer_assignment(customization, fake_redis):
    """Closing an old ticket message must not wipe an assignment that was
    re-anchored to a newer ticket of the same user (agy review round 4)."""
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 600})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)  # stale button
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent1"

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent1")
    )

    assert json.loads(fake_redis.store[key]) == {
        "agent": "agent2",
        "ticket_msg": 600,
    }


@pytest.mark.asyncio
async def test_tickets_answers_on_redis_scan_failure(customization, monkeypatch):
    class BrokenRedis:
        async def scan_iter(self, match, count=None):
            raise ConnectionError("redis down")
            yield

    monkeypatch.setattr(helper_mod, "_get_redis", lambda: BrokenRedis())
    message = _make_message_mock(text="/tickets", bot=_make_bot())

    await customization.cmd_tickets(message, _make_settings())

    assert "редис недоступен" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_tickets_deduplicates_scan_keys(customization, monkeypatch):
    class DuplicateScanRedis(FakeRedis):
        async def scan_iter(self, match, count=None):
            for key in list(self.store):
                yield key  # SCAN is allowed to return duplicates
                yield key

    duplicate = DuplicateScanRedis()
    duplicate.store[f"helper:assign:{HELPER_BOT_ID}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    monkeypatch.setattr(helper_mod, "_get_redis", lambda: duplicate)
    message = _make_message_mock(text="/tickets", bot=_make_bot())

    await customization.cmd_tickets(message, _make_settings())

    text = message.answer.await_args.args[0]
    assert text.count("#ID777") == 1


@pytest.mark.asyncio
async def test_take_blocked_when_already_taken_by_other_agent(
    customization, fake_redis
):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=900)
    callback.bot = bot
    callback.from_user.username = "agent2"

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    # first agent's assignment survives, no event sent, no overwrite
    assert json.loads(fake_redis.store[f"helper:assign:{bot.id}:777"]) == {
        "agent": "agent1",
        "ticket_msg": 556,
    }
    bot.send_message.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_take_by_same_agent_reanchors(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=900)
    callback.bot = bot
    callback.from_user.username = "agent1"

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    assert json.loads(fake_redis.store[key]) == {
        "agent": "agent1",
        "ticket_msg": 900,
    }


@pytest.mark.asyncio
async def test_foreign_mention_tolerates_username_with_at_prefix(
    customization, fake_redis
):
    settings = _make_settings()
    settings.username = "@mtl_helper_bot"
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    message = _make_message_mock(text="/tickets@mtl_helper_bot", bot=bot)

    handled = await customization.handle_master_message(message, settings)

    assert handled is True


@pytest.mark.asyncio
async def test_tickets_plain_id_for_non_supergroup_chat(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    message = _make_message_mock(text="/tickets", bot=bot, chat_id=-42)

    await customization.cmd_tickets(message, _make_settings())

    text = message.answer.await_args.args[0]
    assert "<a href=" not in text
    assert "556" in text


# --- findings from the agy review, round 5 ---


@pytest.mark.asyncio
async def test_take_rolls_back_and_alerts_when_event_send_fails(
    customization, fake_redis
):
    bot = _make_bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    callback = AsyncMock()
    callback.message = _ticket_message_mock()
    callback.bot = bot
    callback.from_user.username = "agent1"

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    assert not fake_redis.store  # claim rolled back, take can be retried
    callback.answer.assert_awaited_once()
    assert "Не удалось закрепить" in callback.answer.await_args.args[0]
    assert callback.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_take_nx_lost_alerts_without_overwriting(
    customization, fake_redis, monkeypatch
):
    """Simulates the race: the guard saw no assignment, but by the time of
    the atomic SET NX another agent's claim already exists."""
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock()
    callback.bot = bot
    callback.from_user.username = "agent2"

    async def stale_read(*, bot_id, user_id):
        return None  # guard passes on a stale read

    monkeypatch.setattr(helper_mod, "_get_assignment", stale_read)

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    assert json.loads(fake_redis.store[key]) == {
        "agent": "agent1",
        "ticket_msg": 556,
    }
    callback.answer.assert_awaited_once()
    assert "уже взят" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_ticket_close_stale_anchor_not_deleted(
    customization, fake_redis, monkeypatch
):
    """The scan found the ticket, but by delete time the assignment was
    re-anchored to a newer ticket — the close must not wipe it."""
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 556})

    async def newer_assignment(*, bot_id, user_id):
        return helper_mod.Assignment(agent="agent2", ticket_msg=600)

    monkeypatch.setattr(helper_mod, "_get_assignment", newer_assignment)
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings())

    assert json.loads(fake_redis.store[key]) == {
        "agent": "agent2",
        "ticket_msg": 556,
    }
    assert "уже закрыт или закреплён" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_private_command_text_is_forwarded_not_handled(fake_redis, repo):
    """/tickets typed by a user in a private chat is a ticket, not a command:
    handle_master_message is master-chat-only by contract."""
    from bot.customizations.default import DefaultBotCustomization
    from bot.customizations.registry import get_customization

    bot = _make_bot()
    user = types.User(id=777, is_bot=False, first_name="Client", username="client")
    message = MagicMock()
    message.bot = bot
    message.text = "/tickets"

    customization = get_customization(bot.id)
    assert isinstance(customization, HelperCustomization)

    # the core only calls the hook for master-chat messages; in private the
    # message goes to cmd_resend and is forwarded as a ticket
    handled = await DefaultBotCustomization().handle_master_message(
        user, _make_settings()
    )
    assert handled is False
    assert user.id == 777 and user.username == "client"

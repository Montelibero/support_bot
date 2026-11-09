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
from types import SimpleNamespace
from typing import Any
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


class _IndexedStore(dict):
    """Test store that mirrors production's per-user ticket index."""

    def __init__(self, owner: "FakeRedis"):
        super().__init__()
        self.owner = owner

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.owner._index_key(key)

    def pop(self, key, *args):
        existed = key in self
        result = super().pop(key, *args)
        if existed:
            self.owner._unindex_key(key)
        return result

    def clear(self):
        super().clear()
        self.owner.index.clear()


class _FakePipeline:
    def __init__(self, owner: "FakeRedis"):
        self.owner = owner
        self.ops: list[tuple[str, tuple, dict]] = []

    def delete(self, *keys):
        self.ops.append(("delete", keys, {}))
        return self

    def set(self, key, value, nx=False, ex=None):
        self.ops.append(("set", (key, value), {"nx": nx, "ex": ex}))
        return self

    def sadd(self, key, *values):
        self.ops.append(("sadd", (key, *values), {}))
        return self

    def srem(self, key, *values):
        self.ops.append(("srem", (key, *values), {}))
        return self

    async def execute(self):
        results = []
        for op, args, kwargs in self.ops:
            results.append(await getattr(self.owner, op)(*args, **kwargs))
        return results

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class FakeRedis:
    def __init__(self):
        self.index: dict[str, set[str]] = {}
        self.store: dict[str, str] = _IndexedStore(self)
        self.scan_calls = 0

    def _index_key(self, key: str) -> None:
        parts = key.split(":")
        if len(parts) != 5 or parts[:2] != ["helper", "assign"]:
            return
        index_key = f"helper:user_tickets:{parts[2]}:{parts[3]}"
        self.index.setdefault(index_key, set()).add(parts[4])

    def _unindex_key(self, key: str) -> None:
        parts = key.split(":")
        if len(parts) != 5 or parts[:2] != ["helper", "assign"]:
            return
        index_key = f"helper:user_tickets:{parts[2]}:{parts[3]}"
        values = self.index.get(index_key)
        if values is not None:
            values.discard(parts[4])
            if not values:
                self.index.pop(index_key, None)

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def mget(self, keys):
        return [self.store.get(key) for key in keys]

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None  # SET NX refuses to overwrite an existing key
        # Bypass _IndexedStore auto-indexing: production Redis.set does not
        # update the user-ticket set either. Only explicit sadd() should.
        dict.__setitem__(self.store, key, value)
        return True

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    async def eval(self, script, numkeys, *args):
        if numkeys != 2:
            raise NotImplementedError("only claim script is supported")
        ticket_key, index_key = args[:2]
        payload, ticket_id = args[2], args[3]
        if ticket_key in self.store:
            return 0
        dict.__setitem__(self.store, ticket_key, payload)
        self.index.setdefault(index_key, set()).add(str(ticket_id))
        return 1

    async def sadd(self, key, *values):
        target = self.index.setdefault(key, set())
        before = len(target)
        target.update(str(value) for value in values)
        return len(target) - before

    async def srem(self, key, *values):
        target = self.index.get(key)
        if target is None:
            return 0
        before = len(target)
        target.difference_update(str(value) for value in values)
        if not target:
            self.index.pop(key, None)
        return before - len(target)

    async def smembers(self, key):
        return set(self.index.get(key, set()))

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            if key in self.store:
                # Bypass _IndexedStore.pop auto-indexing: production Redis
                # delete does not update the user-ticket set either.
                dict.__delitem__(self.store, key)
                deleted += 1
        return deleted

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


@pytest.fixture(autouse=True)
def _clear_helper_pending_state():
    yield
    for task in list(helper_mod._PENDING_TASKS.values()):
        task.cancel()
    helper_mod._PENDING_ACKS.clear()
    helper_mod._PENDING_TASKS.clear()


@pytest.fixture
def customization() -> Any:
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
    message.chat.username = None
    message.bot = bot if bot is not None else _make_bot()
    message.text = text
    message.from_user = None
    message.answer = AsyncMock()
    return message


def _ticket_message_mock(message_id=556):
    message = MagicMock()
    message.chat.id = MASTER_CHAT_ID
    message.chat.username = None
    message.message_thread_id = None
    message.message_id = message_id
    message.media_group_id = None
    message.text = "ticket #ID777"
    message.get_url = MagicMock(return_value="https://t.me/c/1234567890/556")
    return message


def _make_update(update_id: int, payload: dict, bot) -> types.Update:
    return types.Update.model_validate(
        {"update_id": update_id, **payload}, context={"bot": bot}
    )


def _make_take_repo(*, chat_from_id: int = 777, ticket_msg_id: int | None = None):
    repo = MagicMock()
    repo.get_message_resend_info = AsyncMock(
        return_value=SimpleNamespace(
            chat_from_id=chat_from_id, message_id=ticket_msg_id
        )
    )
    repo.get_user_info = AsyncMock(return_value=None)
    return repo


def _make_take_message(
    target: MagicMock | None,
    *,
    bot: AsyncMock,
    username: str | None = "agent2",
    user_id: int = 888,
) -> MagicMock:
    message = _make_message_mock(text="/take", bot=bot)
    message.reply_to_message = target
    message.from_user = MagicMock(id=user_id, username=username, is_bot=False)
    return message


def _make_ticket_bot() -> AsyncMock:
    bot = _make_bot()
    bot.get_chat = AsyncMock(return_value=MagicMock(username="client"))
    return bot


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

    key = f"helper:assign:{bot.id}:777:556"
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
async def test_tickets_blank_line_between_assignments(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    fake_redis.store[f"helper:assign:{bot.id}:778"] = json.dumps(
        {"agent": "agent2", "ticket_msg": 100}
    )
    message = _make_message_mock(text="/tickets", bot=bot)

    await customization.cmd_tickets(message, _make_settings())

    lines = message.answer.await_args.args[0].splitlines()
    first = next(i for i, line in enumerate(lines) if "ticket_close_100" in line)
    second = next(i for i, line in enumerate(lines) if "ticket_close_556" in line)
    assert second == first + 2
    assert lines[first + 1] == ""


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
async def test_ticket_close_foreign_mention_consumed_by_hook(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556@some_other_bot", bot=bot)

    handled = await customization.handle_master_message(message, _make_settings())

    assert handled is True
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
    instance: Any = HelperCustomization()
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
    instance: Any = HelperCustomization()
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
    instance: Any = HelperCustomization()
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
    assert "username" in callback.answer.await_args.args[0].lower()
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
async def test_tickets_with_arguments_is_consumed(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    message = _make_message_mock(text="/tickets please", bot=bot)

    handled = await customization.handle_master_message(
        message, _make_settings(), bot=bot
    )

    assert handled is True
    assert "#ID777" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_tickets_foreign_mention_consumed_by_hook(customization, fake_redis):
    message = _make_message_mock(text="/tickets@some_other_bot")

    handled = await customization.handle_master_message(message, _make_settings())

    assert handled is True
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
async def test_ticket_close_delete_failure_degrades(monkeypatch, customization):
    class DeleteBrokenRedis(FakeRedis):
        async def delete(self, *keys):
            raise ConnectionError("redis down on delete")

    broken = DeleteBrokenRedis()
    monkeypatch.setattr(helper_mod, "_get_redis", lambda: broken)
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777"
    broken.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings())

    assert key in broken.store  # delete failed, assignment kept
    assert "Redis недоступен" in message.answer.await_args.args[0]


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
    """Closing an old ticket message must not wipe another ticket of the
    same user."""
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:600"
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
    assert callback.answer.await_args.args[0] == "Задача уже закрыта"


@pytest.mark.asyncio
async def test_tickets_answers_on_redis_scan_failure(customization, monkeypatch):
    class BrokenRedis:
        async def scan_iter(self, match, count=None):
            raise ConnectionError("redis down")
            yield

    monkeypatch.setattr(helper_mod, "_get_redis", lambda: BrokenRedis())
    message = _make_message_mock(text="/tickets", bot=_make_bot())

    await customization.cmd_tickets(message, _make_settings())

    assert "Redis недоступен" in message.answer.await_args.args[0]


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
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.username = "agent2"

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    # first agent's assignment survives, no event sent, no overwrite
    assert json.loads(fake_redis.store[key]) == {
        "agent": "agent1",
        "ticket_msg": 556,
    }
    bot.send_message.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    bot.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
async def test_take_by_same_agent_is_idempotent(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent1"

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    assert json.loads(fake_redis.store[key]) == {
        "agent": "agent1",
        "ticket_msg": 556,
    }
    bot.send_message.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert "уже" in callback.answer.await_args.args[0]


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
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock()
    callback.bot = bot
    callback.from_user.username = "agent2"

    async def stale_read(*, bot_id, user_id, ticket_msg_id, raise_on_error=False):
        return None  # guard passes on a stale read

    monkeypatch.setattr(helper_mod, "_get_ticket_assignment", stale_read)

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
async def test_ticket_close_one_of_two_keeps_other(customization, fake_redis):
    bot = _make_bot()
    first = f"helper:assign:{bot.id}:777:556"
    second = f"helper:assign:{bot.id}:777:600"
    fake_redis.store[first] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    fake_redis.store[second] = json.dumps({"agent": "agent2", "ticket_msg": 600})
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings())

    assert first not in fake_redis.store
    assert json.loads(fake_redis.store[second]) == {
        "agent": "agent2",
        "ticket_msg": 600,
    }
    assert "закрыт" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_private_command_text_is_forwarded_not_handled(fake_redis, repo):
    """/tickets typed by a user in a private chat is a ticket, not a command."""
    from bot.routers.supports import router as support_router

    bot = _make_bot()
    dispatcher = Dispatcher()
    dispatcher.include_router(support_router)
    dispatcher.update.middleware(_CoreMiddleware(repo, _make_settings()))
    update = _make_update(
        7,
        {
            "message": {
                "message_id": 7,
                "from": {
                    "id": 777,
                    "is_bot": False,
                    "first_name": "Client",
                    "username": "client",
                },
                "chat": {"id": 777, "type": "private"},
                "date": 1700000000,
                "text": "/tickets",
            }
        },
        bot,
    )

    await dispatcher.feed_update(bot=bot, update=update)

    calls = bot.send_message.await_args_list
    assert calls
    assert any(str(call.kwargs.get("chat_id")) == str(MASTER_CHAT_ID) for call in calls)


# --- /take reply flow ---


@pytest.mark.asyncio
async def test_take_reply_claims_ticket_and_adds_close_button(
    customization, fake_redis
):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    repo = _make_take_repo(ticket_msg_id=556)

    await customization.cmd_take(message, _make_settings(), bot=bot, repo=repo)

    key = f"helper:assign:{bot.id}:777:556"
    assert json.loads(fake_redis.store[key]) == {
        "agent": "agent2",
        "ticket_msg": 556,
    }
    event_text = bot.send_message.await_args.kwargs["text"]
    assert "command=taken" in event_text
    markup = bot.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "Взял agent2"
    assert "закрыть: /ticket_close_556" in message.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_take_reply_keeps_other_ticket_of_same_user(customization, fake_redis):
    bot = _make_ticket_bot()
    old_key = f"helper:assign:{bot.id}:777"
    fake_redis.store[old_key] = json.dumps({"agent": "agent1", "ticket_msg": 500})
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    repo = _make_take_repo(ticket_msg_id=556)

    await customization.cmd_take(message, _make_settings(), bot=bot, repo=repo)

    assert json.loads(fake_redis.store[old_key]) == {
        "agent": "agent1",
        "ticket_msg": 500,
    }
    assert f"helper:assign:{bot.id}:777:556" in fake_redis.store


@pytest.mark.asyncio
async def test_two_tickets_taken_via_take_cc_both_agents(customization, fake_redis):
    bot = _make_ticket_bot()
    first = _make_take_message(
        _ticket_message_mock(message_id=500), bot=bot, username="agent1"
    )
    second = _make_take_message(
        _ticket_message_mock(message_id=556), bot=bot, username="agent2"
    )

    await customization.cmd_take(
        first,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=500),
    )
    await customization.cmd_take(
        second,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    user = types.User(id=777, is_bot=False, first_name="Client")
    extra = await customization.get_extra_text(user, MagicMock(), _make_settings())
    assert extra == "\n/get_info_777@mymtlbot\nCC @agent1 @agent2"


@pytest.mark.asyncio
async def test_concurrent_take_has_one_winner(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    first = _make_take_message(target, bot=bot, username="agent1")
    second = _make_take_message(target, bot=bot, username="agent2")

    await asyncio.gather(
        customization.cmd_take(
            first,
            _make_settings(),
            bot=bot,
            repo=_make_take_repo(ticket_msg_id=556),
        ),
        customization.cmd_take(
            second,
            _make_settings(),
            bot=bot,
            repo=_make_take_repo(ticket_msg_id=556),
        ),
    )

    key = f"helper:assign:{bot.id}:777:556"
    assert key in fake_redis.store
    assert bot.send_message.await_count == 1
    answers = {first.answer.await_args.args[0], second.answer.await_args.args[0]}
    assert any("уже взят" in answer for answer in answers)


@pytest.mark.asyncio
async def test_take_requires_reply(customization, fake_redis):
    bot = _make_ticket_bot()
    message = _make_take_message(None, bot=bot)

    await customization.cmd_take(message, _make_settings(), bot=bot, repo=MagicMock())

    assert "ответом" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_rejects_non_ticket_reply(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    repo = MagicMock()
    repo.get_message_resend_info = AsyncMock(return_value=None)

    await customization.cmd_take(message, _make_settings(), bot=bot, repo=repo)

    assert "не является тикетом" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_requires_agent_username(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot, username=None)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "username" in message.answer.await_args.args[0].lower()
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_rejects_bot_sender(customization, fake_redis):
    bot = _make_ticket_bot()
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)
    message.from_user.is_bot = True

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "username" in message.answer.await_args.args[0].lower()
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_rejects_ticket_already_owned_by_other_agent(
    customization, fake_redis
):
    bot = _make_ticket_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "уже взят" in message.answer.await_args.args[0]
    assert json.loads(fake_redis.store[key]) == {
        "agent": "agent1",
        "ticket_msg": 556,
    }
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_take_event_failure_rolls_back_claim(customization, fake_redis):
    bot = _make_ticket_bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert not fake_redis.store
    assert "ошибка отправки" in message.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_extra_text_ccs_all_active_agents(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777:500"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 500}
    )
    fake_redis.store[f"helper:assign:{bot.id}:777:556"] = json.dumps(
        {"agent": "agent2", "ticket_msg": 556}
    )
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot\nCC @agent1 @agent2"


@pytest.mark.asyncio
async def test_extra_text_ccs_mixed_legacy_and_ticket_keys(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 500}
    )
    fake_redis.store[f"helper:assign:{bot.id}:777:556"] = json.dumps(
        {"agent": "agent2", "ticket_msg": 556}
    )
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot\nCC @agent1 @agent2"


@pytest.mark.asyncio
async def test_extra_text_and_reply_markup_use_index_without_scan(
    customization, fake_redis
):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777:556"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    await customization.get_extra_text(user, message, _make_settings())
    await customization.get_reply_markup(user, message, _make_settings())

    assert fake_redis.scan_calls == 0


@pytest.mark.asyncio
async def test_tickets_lists_two_tickets_of_same_user(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777:500"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 500}
    )
    fake_redis.store[f"helper:assign:{bot.id}:777:556"] = json.dumps(
        {"agent": "agent2", "ticket_msg": 556}
    )
    message = _make_message_mock(text="/tickets", bot=bot)

    await customization.cmd_tickets(message, _make_settings())

    text = message.answer.await_args.args[0]
    assert text.count("#ID777") == 2
    assert "/ticket_close_500" in text
    assert "/ticket_close_556" in text


@pytest.mark.asyncio
async def test_dispatcher_take_command_claims_ticket(fake_redis, repo):
    from bot.routers.supports import router as support_router

    repo.messages.append(
        {
            "bot_id": HELPER_BOT_ID,
            "user_id": None,
            "message_id": 42,
            "resend_id": 556,
            "chat_from_id": 777,
            "chat_for_id": MASTER_CHAT_ID,
        }
    )
    settings = _make_settings()
    bot = _make_ticket_bot()
    dispatcher = Dispatcher()
    dispatcher.include_router(support_router)
    dispatcher.update.middleware(_CoreMiddleware(repo, settings))
    update = _make_update(
        3,
        {
            "message": {
                "message_id": 3,
                "from": {
                    "id": 888,
                    "is_bot": False,
                    "first_name": "Agent",
                    "username": "agent2",
                },
                "chat": {
                    "id": MASTER_CHAT_ID,
                    "type": "supergroup",
                    "title": "M",
                },
                "date": 1700000000,
                "text": "/take",
                "reply_to_message": {
                    "message_id": 556,
                    "from": {
                        "id": HELPER_BOT_ID,
                        "is_bot": True,
                        "first_name": "Helper",
                        "username": "mtl_helper_bot",
                    },
                    "chat": {
                        "id": MASTER_CHAT_ID,
                        "type": "supergroup",
                        "title": "M",
                    },
                    "date": 1700000000,
                    "text": "ticket #ID777",
                },
            }
        },
        bot,
    )

    await dispatcher.feed_update(bot=bot, update=update)

    key = f"helper:assign:{bot.id}:777:556"
    assert json.loads(fake_redis.store[key]) == {
        "agent": "agent2",
        "ticket_msg": 556,
    }
    bot.edit_message_reply_markup.assert_awaited()


@pytest.mark.asyncio
async def test_close_button_closes_take_claimed_ticket(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent2")
    )

    assert key not in fake_redis.store
    assert "command=closed" in bot.send_message.await_args.kwargs["text"]
    markup = bot.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "Закрыл agent2"


@pytest.mark.asyncio
async def test_reply_markup_returns_after_all_tickets_closed(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777:556"] = json.dumps(
        {"agent": "agent1", "ticket_msg": 556}
    )
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    assert await customization.get_reply_markup(user, message, _make_settings()) is None

    fake_redis.store.clear()

    markup = await customization.get_reply_markup(user, message, _make_settings())
    assert markup is not None
    assert markup.inline_keyboard[0][0].text == "Взять"


@pytest.mark.asyncio
async def test_reply_markup_stays_hidden_until_last_ticket_closed(
    customization, fake_redis
):
    bot = _make_bot()
    first = f"helper:assign:{bot.id}:777:500"
    second = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[first] = json.dumps({"agent": "agent1", "ticket_msg": 500})
    fake_redis.store[second] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    assert await customization.get_reply_markup(user, message, _make_settings()) is None

    fake_redis.store.pop(first)
    assert await customization.get_reply_markup(user, message, _make_settings()) is None

    fake_redis.store.pop(second)
    markup = await customization.get_reply_markup(user, message, _make_settings())
    assert markup is not None
    assert markup.inline_keyboard[0][0].text == "Взять"


@pytest.mark.asyncio
async def test_button_reappears_after_command_close(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    close_message = _make_message_mock(text="/ticket_close_556", bot=bot)
    await customization.cmd_ticket_close(close_message, _make_settings())
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    markup = await customization.get_reply_markup(user, message, _make_settings())

    assert markup is not None
    assert markup.inline_keyboard[0][0].text == "Взять"


@pytest.mark.asyncio
async def test_take_command_failure_never_falls_through(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    customization.cmd_take = AsyncMock(side_effect=RuntimeError("boom"))

    handled = await customization.handle_master_message(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert handled is True
    assert "Не удалось обработать команду" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_rejects_invalid_ticket_url(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    target.get_url.return_value = ""
    target.chat.id = -42
    target.chat.username = None
    message = _make_take_message(target, bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "URL" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_rejects_arguments(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    message.text = "/take 123"

    handled = await customization.handle_master_message(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert handled is True
    assert "не принимает аргументов" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_foreign_mention_is_consumed(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    message.text = "/take@some_other_bot"

    handled = await customization.handle_master_message(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert handled is True
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_with_id_suffix_is_consumed_without_claim(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    message.text = "/take_123"

    handled = await customization.handle_master_message(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert handled is True
    assert "без ID" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_close_legacy_ticket_event_failure_restores_per_ticket_key(
    customization, fake_redis
):
    bot = _make_bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    legacy = f"helper:assign:{bot.id}:777"
    restored = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[legacy] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent2")
    )

    assert legacy not in fake_redis.store
    assert json.loads(fake_redis.store[restored]) == {
        "agent": "agent2",
        "ticket_msg": 556,
    }
    assert fake_redis.index[f"helper:user_tickets:{bot.id}:777"] == {"556"}


@pytest.mark.asyncio
async def test_take_legacy_same_ticket_by_same_agent_is_idempotent(
    customization, fake_redis
):
    bot = _make_ticket_bot()
    legacy = f"helper:assign:{bot.id}:777"
    fake_redis.store[legacy] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "уже закреплён" in message.answer.await_args.args[0]
    assert f"helper:assign:{bot.id}:777:556" not in fake_redis.store
    bot.send_message.assert_not_awaited()
    bot.edit_message_reply_markup.assert_awaited()


@pytest.mark.asyncio
async def test_take_legacy_same_ticket_by_other_agent_is_blocked(
    customization, fake_redis
):
    bot = _make_ticket_bot()
    legacy = f"helper:assign:{bot.id}:777"
    fake_redis.store[legacy] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "уже взят" in message.answer.await_args.args[0]
    assert f"helper:assign:{bot.id}:777:556" not in fake_redis.store
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_tickets_deduplicates_legacy_and_ticket_keys(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "legacy_agent", "ticket_msg": 556}
    )
    fake_redis.store[f"helper:assign:{bot.id}:777:556"] = json.dumps(
        {"agent": "ticket_agent", "ticket_msg": 556}
    )
    message = _make_message_mock(text="/tickets", bot=bot)

    await customization.cmd_tickets(message, _make_settings())

    text = message.answer.await_args.args[0]
    assert text.count("#ID777") == 1
    assert "@ticket_agent" in text
    assert "@legacy_agent" not in text


@pytest.mark.asyncio
async def test_cc_prefers_ticket_key_over_legacy_same_ticket(customization, fake_redis):
    bot = _make_bot()
    fake_redis.store[f"helper:assign:{bot.id}:777"] = json.dumps(
        {"agent": "legacy_agent", "ticket_msg": 556}
    )
    fake_redis.store[f"helper:assign:{bot.id}:777:556"] = json.dumps(
        {"agent": "ticket_agent", "ticket_msg": 556}
    )
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot\nCC @ticket_agent"


@pytest.mark.asyncio
async def test_corrupted_ticket_index_value_is_skipped(customization, fake_redis):
    bot = _make_bot()
    fake_redis.index[f"helper:user_tickets:{bot.id}:777"] = {"invalid_id"}
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot"
    assert f"helper:user_tickets:{bot.id}:777" not in fake_redis.index


@pytest.mark.asyncio
async def test_stale_ticket_index_id_is_cleaned(customization, fake_redis):
    bot = _make_bot()
    index = f"helper:user_tickets:{bot.id}:777"
    fake_redis.index[index] = {"999"}
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot"
    assert index not in fake_redis.index


@pytest.mark.asyncio
async def test_corrupted_ticket_value_is_removed_from_index(customization, fake_redis):
    bot = _make_bot()
    index = f"helper:user_tickets:{bot.id}:777"
    fake_redis.index[index] = {"556"}
    fake_redis.store[f"helper:assign:{bot.id}:777:556"] = "corrupt"
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot"
    assert index not in fake_redis.index


@pytest.mark.asyncio
async def test_extra_text_deduplicates_agent_across_tickets(customization, fake_redis):
    bot = _make_bot()
    for ticket in (500, 556):
        fake_redis.store[f"helper:assign:{bot.id}:777:{ticket}"] = json.dumps(
            {"agent": "agent1", "ticket_msg": ticket}
        )
    user = types.User(id=777, is_bot=False, first_name="Client")
    message = MagicMock()
    message.bot = bot

    result = await customization.get_extra_text(user, message, _make_settings())

    assert result == "\n/get_info_777@mymtlbot\nCC @agent1"


@pytest.mark.asyncio
async def test_take_redis_claim_failure_degrades(
    customization, fake_redis, monkeypatch
):
    class EvalBrokenRedis(FakeRedis):
        async def eval(self, script, numkeys, *args):
            raise ConnectionError("redis down on eval")

    broken = EvalBrokenRedis()
    monkeypatch.setattr(helper_mod, "_get_redis", lambda: broken)
    bot = _make_ticket_bot()
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "Redis недоступен" in message.answer.await_args.args[0]
    assert not broken.store


@pytest.mark.asyncio
async def test_take_survives_close_button_edit_failure(customization, fake_redis):
    bot = _make_ticket_bot()
    bot.edit_message_reply_markup = AsyncMock(side_effect=RuntimeError("edit failed"))
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert f"helper:assign:{bot.id}:777:556" in fake_redis.store
    assert "закрыть: /ticket_close_556" in message.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_take_get_chat_failure_falls_back_to_dash(customization, fake_redis):
    bot = _make_ticket_bot()
    bot.get_chat = AsyncMock(side_effect=RuntimeError("profile hidden"))
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert f"helper:assign:{bot.id}:777:556" in fake_redis.store
    assert "username=- " in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_take_assignment_read_failure_degrades(
    customization, fake_redis, monkeypatch
):
    bot = _make_ticket_bot()
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)

    async def lost_claim(**kwargs):
        return False

    async def broken_read(**kwargs):
        raise ConnectionError("redis down on read")

    monkeypatch.setattr(helper_mod, "_claim_ticket", lost_claim)
    monkeypatch.setattr(helper_mod, "_get_ticket_assignment", broken_read)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "Redis недоступен" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_empty_text_is_ignored(customization, fake_redis):
    bot = _make_ticket_bot()
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)
    message.text = "   "

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert not message.answer.await_count
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_ticket_close_uses_repo_lookup(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    repo = MagicMock()
    repo.get_message_resend_info = AsyncMock(
        return_value=SimpleNamespace(chat_from_id=777)
    )
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings(), repo=repo)

    assert key not in fake_redis.store
    repo.get_message_resend_info.assert_awaited_once()
    assert "закрыт" in message.answer.await_args.args[0]
    assert "command=closed" in bot.send_message.await_args.kwargs["text"]
    assert ("closed", "https://t.me/c/1234567890/556") in helper_mod._PENDING_ACKS


@pytest.mark.asyncio
async def test_ticket_close_button_shows_closer(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "alice", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556", bot=bot)
    message.from_user = MagicMock(username="bob")

    await customization.cmd_ticket_close(message, _make_settings(), bot=bot)

    markup = bot.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "Закрыл bob"
    assert "agent_username=bob" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_ticket_close_without_username_uses_assignee(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "alice", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings(), bot=bot)

    markup = bot.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "Закрыл alice"


@pytest.mark.asyncio
async def test_ticket_close_repo_known_but_redis_missing(customization, fake_redis):
    bot = _make_bot()
    repo = MagicMock()
    repo.get_message_resend_info = AsyncMock(
        return_value=SimpleNamespace(chat_from_id=777)
    )
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings(), repo=repo)

    assert "не найден" in message.answer.await_args.args[0]
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_ticket_close_falls_back_to_redis_when_db_missing(
    customization, fake_redis
):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    repo = MagicMock()
    repo.get_message_resend_info = AsyncMock(return_value=None)
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings(), repo=repo)

    assert key not in fake_redis.store
    assert "закрыт" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_ticket_close_db_user_mismatch_does_not_scan(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:888:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    repo = MagicMock()
    repo.get_message_resend_info = AsyncMock(
        return_value=SimpleNamespace(chat_from_id=777)
    )
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings(), repo=repo)

    assert key in fake_redis.store
    assert "не найден" in message.answer.await_args.args[0]
    assert fake_redis.scan_calls == 0


@pytest.mark.asyncio
async def test_ticket_close_invalid_url_keeps_ticket(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556", bot=bot, chat_id=-42)
    message.chat.username = None

    await customization.cmd_ticket_close(message, _make_settings(), bot=bot)

    assert key in fake_redis.store
    assert "URL" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_ticket_close_preserves_forum_thread(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556", bot=bot)
    message.message_thread_id = 777

    await customization.cmd_ticket_close(message, _make_settings(), bot=bot)

    pending = helper_mod._PENDING_ACKS[("closed", "https://t.me/c/1234567890/556")]
    assert pending.master_thread_id == 777


@pytest.mark.asyncio
async def test_closed_ticket_cannot_be_retaken(customization, fake_redis):
    bot = _make_ticket_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    close_message = _make_message_mock(text="/ticket_close_556", bot=bot)
    await customization.cmd_ticket_close(close_message, _make_settings())
    assert key not in fake_redis.store

    target = _ticket_message_mock(message_id=556)
    target.reply_markup = helper_mod._closed_reply_markup(777, "agent1")
    take_message = _make_take_message(target, bot=bot)
    await customization.cmd_take(
        take_message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert key not in fake_redis.store
    assert "уже закрыт" in take_message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_closed_ticket_button_click_keeps_keyboard(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.message.reply_markup = helper_mod._closed_reply_markup(777, "agent1")
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    assert key not in fake_redis.store
    bot.send_message.assert_not_awaited()
    bot.edit_message_reply_markup.assert_not_awaited()
    assert callback.answer.await_args.args[0] == "Тикет уже закрыт"


@pytest.mark.asyncio
async def test_public_supergroup_url_is_consistent(customization, fake_redis):
    target = _ticket_message_mock(message_id=556)
    target.chat.username = "public_master"
    expected = "https://t.me/public_master/556"

    assert helper_mod._extract_message_url(target) == expected
    assert helper_mod._message_url(target.chat, 556) == expected


@pytest.mark.asyncio
async def test_closed_ticket_without_keyboard_can_be_retaken(customization, fake_redis):
    bot = _make_ticket_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    close_message = _make_message_mock(text="/ticket_close_556", bot=bot)
    await customization.cmd_ticket_close(close_message, _make_settings())
    assert key not in fake_redis.store

    target = _ticket_message_mock(message_id=556)
    target.reply_markup = None
    take_message = _make_take_message(target, bot=bot)
    await customization.cmd_take(
        take_message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert key in fake_redis.store
    assert "взят" in take_message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_closed_button_click_is_answered(customization, fake_redis):
    bot = _make_bot()
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=0, username="agent1")
    )

    callback.answer.assert_awaited_once()
    assert "Задача закрыта" in callback.answer.await_args.args[0]
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_take_repo_failure_degrades(customization, fake_redis):
    bot = _make_ticket_bot()
    repo = MagicMock()
    repo.get_message_resend_info = AsyncMock(side_effect=RuntimeError("db down"))
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)

    await customization.cmd_take(message, _make_settings(), bot=bot, repo=repo)

    assert "ошибка базы" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_missing_repo_answers_error(customization, fake_redis):
    bot = _make_ticket_bot()
    message = _make_take_message(_ticket_message_mock(message_id=556), bot=bot)

    await customization.cmd_take(message, _make_settings(), bot=bot)

    assert "внутренняя зависимость" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_close_button_keyboard_edit_failure_still_answers(
    customization, fake_redis
):
    bot = _make_bot()
    bot.edit_message_reply_markup = AsyncMock(side_effect=RuntimeError("edit failed"))
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent2")
    )

    assert key not in fake_redis.store
    callback.answer.assert_awaited()
    assert "умничка" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_delete_missing_ticket_assignment_returns_false(
    customization, fake_redis
):
    deleted = await helper_mod._delete_ticket_assignment(
        bot_id=HELPER_BOT_ID, user_id=777, ticket_msg_id=556
    )

    assert deleted is False


@pytest.mark.asyncio
async def test_close_second_click_does_not_send_duplicate_event(
    customization, fake_redis
):
    bot = _make_bot()
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 556})

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent2")
    )
    first_event_count = bot.send_message.await_count
    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent2")
    )

    assert first_event_count == 1
    assert bot.send_message.await_count == first_event_count
    assert callback.answer.await_args.args[0] == "Задача уже закрыта"
    assert callback.answer.await_args.kwargs.get("show_alert") is not True
    markup = bot.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "Закрыл agent2"


@pytest.mark.asyncio
async def test_ticket_close_rejects_caption_command(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    message = _make_message_mock(text=None, bot=bot)
    message.caption = "/ticket_close_556"

    handled = await customization.handle_master_message(
        message, _make_settings(), bot=bot
    )

    assert handled is True
    assert "вложения" in message.answer.await_args.args[0]
    assert key in fake_redis.store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["/ticket_close", "/ticket_close_", "/ticket_close 123"]
)
async def test_ticket_close_typos_are_consumed(customization, text):
    bot = _make_bot()
    message = _make_message_mock(text=text, bot=bot)

    handled = await customization.handle_master_message(
        message, _make_settings(), bot=bot
    )

    assert handled is True
    assert "формате" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_ticket_close_one_ticket_keeps_legacy_other(customization, fake_redis):
    bot = _make_bot()
    legacy = f"helper:assign:{bot.id}:777"
    new = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[legacy] = json.dumps({"agent": "agent1", "ticket_msg": 500})
    fake_redis.store[new] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_556", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings())

    assert new not in fake_redis.store
    assert json.loads(fake_redis.store[legacy]) == {
        "agent": "agent1",
        "ticket_msg": 500,
    }


@pytest.mark.asyncio
async def test_ticket_close_legacy_one_keeps_new_ticket(customization, fake_redis):
    bot = _make_bot()
    legacy = f"helper:assign:{bot.id}:777"
    new = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[legacy] = json.dumps({"agent": "agent1", "ticket_msg": 500})
    fake_redis.store[new] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    message = _make_message_mock(text="/ticket_close_500", bot=bot)

    await customization.cmd_ticket_close(message, _make_settings())

    assert legacy not in fake_redis.store
    assert json.loads(fake_redis.store[new]) == {
        "agent": "agent2",
        "ticket_msg": 556,
    }


@pytest.mark.asyncio
async def test_close_button_one_of_two_keeps_other(customization, fake_redis):
    bot = _make_bot()
    first = f"helper:assign:{bot.id}:777:500"
    second = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[first] = json.dumps({"agent": "agent1", "ticket_msg": 500})
    fake_redis.store[second] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent2")
    )

    assert second not in fake_redis.store
    assert first in fake_redis.store
    user = types.User(id=777, is_bot=False, first_name="Client")
    extra = await customization.get_extra_text(user, MagicMock(), _make_settings())
    assert extra.endswith("CC @agent1")


@pytest.mark.asyncio
async def test_close_button_allows_agent_username_change(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent1", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent1_renamed"

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent1")
    )

    assert key not in fake_redis.store
    assert "command=closed" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_close_button_username_is_case_insensitive(customization, fake_redis):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "Agent_Smith", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent_smith"

    await customization.callbacks_lang_end(
        callback,
        EndCallbackData(ticket_user_id=777, user_id=888, username="Agent_Smith"),
    )

    assert key not in fake_redis.store
    assert "command=closed" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_srem_failure_does_not_block_close(customization, fake_redis):
    class SremBrokenRedis(FakeRedis):
        async def srem(self, key, *values):
            raise ConnectionError("redis down on srem")

    broken = SremBrokenRedis()
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    broken.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"
    original = helper_mod._get_redis
    helper_mod._get_redis = lambda: broken
    try:
        await customization.callbacks_lang_end(
            callback,
            EndCallbackData(ticket_user_id=777, user_id=888, username="agent2"),
        )
    finally:
        helper_mod._get_redis = original

    assert key not in broken.store
    assert "command=closed" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_claim_eval_failure_leaves_no_key(customization, fake_redis, monkeypatch):
    class EvalBrokenRedis(FakeRedis):
        async def eval(self, script, numkeys, *args):
            raise ConnectionError("redis down on eval")

    broken = EvalBrokenRedis()
    monkeypatch.setattr(helper_mod, "_get_redis", lambda: broken)

    with pytest.raises(ConnectionError):
        await helper_mod._claim_ticket(
            bot_id=HELPER_BOT_ID,
            user_id=777,
            agent_username="agent1",
            ticket_msg_id=556,
        )

    assert f"helper:assign:{HELPER_BOT_ID}:777:556" not in broken.store


@pytest.mark.asyncio
async def test_claim_and_delete_keep_user_ticket_index(customization, fake_redis):
    bot = _make_bot()
    index = f"helper:user_tickets:{bot.id}:777"

    claimed = await helper_mod._claim_ticket(
        bot_id=bot.id, user_id=777, agent_username="agent1", ticket_msg_id=556
    )
    assert claimed is True
    assert fake_redis.index[index] == {"556"}

    deleted = await helper_mod._delete_ticket_assignment(
        bot_id=bot.id, user_id=777, ticket_msg_id=556
    )
    assert deleted is True
    assert index not in fake_redis.index


@pytest.mark.asyncio
async def test_clean_user_reads_index_without_scan(customization, fake_redis):
    assignments = await helper_mod._get_assignments_for_user(
        bot_id=HELPER_BOT_ID, user_id=777
    )

    assert assignments == []
    assert fake_redis.scan_calls == 0


@pytest.mark.asyncio
async def test_click_after_command_close_removes_stale_keyboard(
    customization, fake_redis
):
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    close_message = _make_message_mock(text="/ticket_close_556", bot=bot)
    await customization.cmd_ticket_close(close_message, _make_settings(), bot=bot)
    bot.edit_message_reply_markup.reset_mock()

    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"
    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent2")
    )

    markup = bot.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "Закрыл agent2"
    assert callback.answer.await_args.args[0] == "Задача уже закрыта"
    assert callback.answer.await_args.kwargs.get("show_alert") is not True


@pytest.mark.asyncio
async def test_take_own_mention_suffix_claims_ticket(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    message.text = "/take@mtl_helper_bot"

    handled = await customization.handle_master_message(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert handled is True
    assert f"helper:assign:{bot.id}:777:556" in fake_redis.store


@pytest.mark.asyncio
async def test_take_allows_leading_whitespace(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    message.text = " /take"

    handled = await customization.handle_master_message(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert handled is True
    assert f"helper:assign:{bot.id}:777:556" in fake_redis.store


@pytest.mark.asyncio
async def test_take_rejects_media_ticket_message(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    target.text = None
    target.caption = None
    target.media_group_id = "album-1"
    message = _make_take_message(target, bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "медиа" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_rejects_media_with_caption_ticket_message(
    customization, fake_redis
):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    target.text = None
    target.caption = "ticket #ID777"
    target.media_group_id = None
    message = _make_take_message(target, bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert "медиа" in message.answer.await_args.args[0]
    assert not fake_redis.store
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_take_caption_command_is_consumed_without_claim(
    customization, fake_redis
):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)
    message.text = None
    message.caption = "/take"

    handled = await customization.handle_master_message(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    assert handled is True
    assert "вложения" in message.answer.await_args.args[0]
    assert not fake_redis.store


@pytest.mark.asyncio
async def test_take_uses_operator_thread_for_ack(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    target.message_thread_id = None
    message = _make_take_message(target, bot=bot)
    message.message_thread_id = 777

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )

    pending = helper_mod._PENDING_ACKS[("taken", "https://t.me/c/1234567890/556")]
    assert pending.master_thread_id == 777


@pytest.mark.asyncio
async def test_take_button_ack_falls_back_to_settings_thread(
    customization, fake_redis, monkeypatch
):
    bot = _make_bot()
    settings = _make_settings()
    settings.master_thread = 777
    monkeypatch.setattr(
        helper_mod.bot_config, "get_bot_setting", lambda bot_id: settings
    )
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.message.message_thread_id = None
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent1"

    await customization.callbacks_lang_get(
        callback, GetCallbackData(user_id=777, username="client")
    )

    pending = helper_mod._PENDING_ACKS[("taken", "https://t.me/c/1234567890/556")]
    assert pending.master_thread_id == 777


@pytest.mark.asyncio
async def test_take_ack_is_shared_between_instances(customization, fake_redis):
    bot = _make_ticket_bot()
    target = _ticket_message_mock(message_id=556)
    message = _make_take_message(target, bot=bot)

    await customization.cmd_take(
        message,
        _make_settings(),
        bot=bot,
        repo=_make_take_repo(ticket_msg_id=556),
    )
    assert ("taken", "https://t.me/c/1234567890/556") in helper_mod._PENDING_ACKS

    other = HelperCustomization()
    dispatcher = Dispatcher()
    dispatcher.include_router(other.router)
    update = _make_update(
        9,
        {
            "channel_post": {
                "message_id": 5,
                "chat": {"id": HELPER_EVENTS_CHAT_ID, "type": "channel"},
                "date": 1700000000,
                "text": (
                    "#helper command=ack op=taken "
                    "url=https://t.me/c/1234567890/556 status=ok"
                ),
            }
        },
        bot,
    )

    await dispatcher.feed_update(bot=bot, update=update)

    assert (
        "taken",
        "https://t.me/c/1234567890/556",
    ) not in helper_mod._PENDING_ACKS


@pytest.mark.asyncio
async def test_ack_reregistration_keeps_new_task(customization):
    bot = _make_bot()
    customization._register_pending_ack(
        op="taken",
        url="race",
        master_chat_id=MASTER_CHAT_ID,
        master_thread_id=None,
        agent_username="agent1",
        bot=bot,
    )
    first = customization._pending_tasks[("taken", "race")]
    customization._register_pending_ack(
        op="taken",
        url="race",
        master_chat_id=MASTER_CHAT_ID,
        master_thread_id=None,
        agent_username="agent1",
        bot=bot,
    )
    second = customization._pending_tasks[("taken", "race")]

    await asyncio.sleep(0)

    assert first is not second
    assert customization._pending_tasks[("taken", "race")] is second
    second.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await second


@pytest.mark.asyncio
async def test_close_button_event_failure_keeps_ticket(customization, fake_redis):
    bot = _make_bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    key = f"helper:assign:{bot.id}:777:556"
    fake_redis.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"

    await customization.callbacks_lang_end(
        callback, EndCallbackData(ticket_user_id=777, user_id=888, username="agent2")
    )

    assert key in fake_redis.store
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    assert "ошибка отправки" in callback.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_close_button_delete_failure_keeps_keyboard(customization, fake_redis):
    class DeleteBrokenRedis(FakeRedis):
        async def delete(self, *keys):
            raise ConnectionError("redis down on delete")

    broken = DeleteBrokenRedis()
    bot = _make_bot()
    key = f"helper:assign:{bot.id}:777:556"
    broken.store[key] = json.dumps({"agent": "agent2", "ticket_msg": 556})
    callback = AsyncMock()
    callback.message = _ticket_message_mock(message_id=556)
    callback.bot = bot
    callback.from_user.id = 888
    callback.from_user.username = "agent2"
    original = helper_mod._get_redis
    helper_mod._get_redis = lambda: broken
    try:
        await customization.callbacks_lang_end(
            callback,
            EndCallbackData(ticket_user_id=777, user_id=888, username="agent2"),
        )
    finally:
        helper_mod._get_redis = original

    assert key in broken.store
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    assert "Redis" in callback.answer.await_args.args[0]
    bot.edit_message_reply_markup.assert_not_awaited()

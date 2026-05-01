from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import (
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
)

from bot.reactions import (
    clear_cache,
    invalidate,
    safe_set_message_reaction,
)


def _make_bot(available_reactions, *, bot_id: int = 42) -> AsyncMock:
    bot = AsyncMock(spec=Bot)
    bot.id = bot_id
    chat_full = MagicMock()
    chat_full.available_reactions = available_reactions
    bot.get_chat.return_value = chat_full
    return bot


def _badrequest(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=MagicMock(spec=TelegramMethod), message=message)


@pytest.mark.asyncio
async def test_emoji_allowed_when_available_reactions_is_none():
    bot = _make_bot(None)
    ok = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👀"),
        log_hint="test",
    )
    assert ok is True
    bot.set_message_reaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_custom_emoji_skipped_when_available_reactions_is_none():
    bot = _make_bot(None)
    ok = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeCustomEmoji(custom_emoji_id="x1"),
        log_hint="test",
    )
    assert ok is False
    bot.set_message_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_whitelist_blocks_everything():
    bot = _make_bot([])
    ok = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    assert ok is False
    bot.set_message_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_whitelist_allows_exact_emoji_only():
    bot = _make_bot([ReactionTypeEmoji(emoji="👍")])

    ok_allowed = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    assert ok_allowed is True

    ok_blocked = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=2,
        reaction=ReactionTypeEmoji(emoji="👀"),
        log_hint="test",
    )
    assert ok_blocked is False

    assert bot.set_message_reaction.await_count == 1


@pytest.mark.asyncio
async def test_whitelist_matches_custom_emoji_by_id():
    bot = _make_bot([ReactionTypeCustomEmoji(custom_emoji_id="abc")])

    ok = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeCustomEmoji(custom_emoji_id="abc"),
        log_hint="test",
    )
    assert ok is True

    ok_wrong = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=2,
        reaction=ReactionTypeCustomEmoji(custom_emoji_id="zzz"),
        log_hint="test",
    )
    assert ok_wrong is False


@pytest.mark.asyncio
async def test_cache_is_used_on_second_call():
    bot = _make_bot([ReactionTypeEmoji(emoji="👍")])

    await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=2,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )

    assert bot.get_chat.await_count == 1


@pytest.mark.asyncio
async def test_invalidate_drops_cache():
    bot = _make_bot([ReactionTypeEmoji(emoji="👍")])

    await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    invalidate(bot.id, -100)
    await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=2,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )

    assert bot.get_chat.await_count == 2


@pytest.mark.asyncio
async def test_reaction_invalid_invalidates_cache_and_skips_silently():
    # Pre-check passes (None = all default allowed), but Telegram still rejects.
    bot = _make_bot(None)
    bot.set_message_reaction.side_effect = _badrequest("Bad Request: REACTION_INVALID")

    ok = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👀"),
        log_hint="test",
    )
    assert ok is False
    # Second call after invalidation re-fetches the chat.
    bot.set_message_reaction.side_effect = None
    chat_full = MagicMock()
    chat_full.available_reactions = [ReactionTypeEmoji(emoji="👀")]
    bot.get_chat.return_value = chat_full

    ok_after = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=2,
        reaction=ReactionTypeEmoji(emoji="👀"),
        log_hint="test",
    )
    assert ok_after is True
    assert bot.get_chat.await_count == 2


@pytest.mark.asyncio
async def test_silent_skip_on_message_not_modified():
    bot = _make_bot(None)
    bot.set_message_reaction.side_effect = _badrequest(
        "Bad Request: message is not modified"
    )
    ok = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_silent_skip_on_message_not_found():
    bot = _make_bot(None)
    bot.set_message_reaction.side_effect = _badrequest(
        "Bad Request: message to react not found"
    )
    ok = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_fail_open_when_get_chat_raises():
    bot = AsyncMock(spec=Bot)
    bot.id = 1
    bot.get_chat.side_effect = _badrequest("Bad Request: chat not found")

    ok = await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👀"),
        log_hint="test",
    )
    # Fail-open: pre-check unknown → still tries set_message_reaction.
    assert ok is True
    bot.set_message_reaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_keyed_by_bot_id():
    bot_a = _make_bot([ReactionTypeEmoji(emoji="👍")], bot_id=1)
    bot_b = _make_bot([], bot_id=2)

    assert await safe_set_message_reaction(
        bot_a,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    assert not await safe_set_message_reaction(
        bot_b,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    # Each bot has its own cache slot.
    assert bot_a.get_chat.await_count == 1
    assert bot_b.get_chat.await_count == 1


@pytest.mark.asyncio
async def test_clear_cache_helper():
    bot = _make_bot([ReactionTypeEmoji(emoji="👍")])
    await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=1,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    clear_cache()
    await safe_set_message_reaction(
        bot,
        chat_id=-100,
        message_id=2,
        reaction=ReactionTypeEmoji(emoji="👍"),
        log_hint="test",
    )
    assert bot.get_chat.await_count == 2

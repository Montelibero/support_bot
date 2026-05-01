"""Safe reaction helpers with per-chat availability cache.

Chat admins can restrict the list of reactions (``available_reactions``).
Attempts to set a reaction outside the whitelist fail with Telegram's
``Bad Request: REACTION_INVALID``. To avoid spamming logs and wasting API
calls, we cache ``getChat.available_reactions`` per ``(bot_id, chat_id)``
with a 1h TTL and pre-filter attempts.
"""

from __future__ import annotations

import time
from asyncio import Lock
from dataclasses import dataclass
from typing import Final

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Message,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
    ReactionTypeUnion,
)
from loguru import logger


CACHE_TTL_SECONDS: Final[int] = 60 * 60

_SILENT_BADREQUEST_FRAGMENTS: Final[tuple[str, ...]] = (
    "message is not modified",
    "message to react not found",
)
_REACTION_INVALID_FRAGMENT: Final[str] = "REACTION_INVALID"


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    allowed: list[ReactionTypeUnion] | None  # ``None`` — all default emoji allowed


class _Unknown:
    """Sentinel returned by :func:`_fetch_allowed` when a lookup fails and the
    caller should fail-open (attempt the API call anyway).
    """


_UNKNOWN: Final[_Unknown] = _Unknown()

_cache: dict[tuple[int, int], _CacheEntry] = {}
_fetch_locks: dict[tuple[int, int], Lock] = {}


def clear_cache() -> None:
    """Drop all cached entries. Intended for tests."""
    _cache.clear()
    _fetch_locks.clear()


def invalidate(bot_id: int, chat_id: int) -> None:
    """Drop cached entry for a specific chat."""
    _cache.pop((bot_id, chat_id), None)


async def _fetch_allowed(
    bot: Bot, chat_id: int
) -> list[ReactionTypeUnion] | None | _Unknown:
    key = (bot.id, chat_id)
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None and entry.expires_at > now:
        return entry.allowed

    lock = _fetch_locks.setdefault(key, Lock())
    async with lock:
        entry = _cache.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.allowed
        try:
            chat_full = await bot.get_chat(chat_id)
        except Exception as ex:
            logger.warning(
                f"get_chat for available_reactions failed — "
                f"bot_id={bot.id}, chat_id={chat_id}: {ex}"
            )
            return _UNKNOWN

        raw = getattr(chat_full, "available_reactions", None)
        allowed: list[ReactionTypeUnion] | None
        if raw is None:
            allowed = None
        elif isinstance(raw, list):
            allowed = raw
        else:
            allowed = None

        _cache[key] = _CacheEntry(expires_at=now + CACHE_TTL_SECONDS, allowed=allowed)
        return allowed


def _reaction_allowed(
    reaction: ReactionTypeUnion, allowed: list[ReactionTypeUnion] | None
) -> bool:
    if allowed is None:
        # All default emoji reactions are allowed; custom emoji requires an
        # explicit chat-level allow, so skip it here.
        return isinstance(reaction, ReactionTypeEmoji)
    if not allowed:
        return False
    for item in allowed:
        if isinstance(reaction, ReactionTypeEmoji) and isinstance(
            item, ReactionTypeEmoji
        ):
            if reaction.emoji == item.emoji:
                return True
        elif isinstance(reaction, ReactionTypeCustomEmoji) and isinstance(
            item, ReactionTypeCustomEmoji
        ):
            if reaction.custom_emoji_id == item.custom_emoji_id:
                return True
    return False


def _describe(reaction: ReactionTypeUnion) -> str:
    if isinstance(reaction, ReactionTypeEmoji):
        return f"emoji={reaction.emoji!r}"
    if isinstance(reaction, ReactionTypeCustomEmoji):
        return f"custom_emoji_id={reaction.custom_emoji_id!r}"
    return f"type={getattr(reaction, 'type', 'unknown')!r}"


async def safe_set_message_reaction(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    reaction: ReactionTypeUnion,
    log_hint: str,
) -> bool:
    """Set a reaction if it is allowed in the chat.

    Returns ``True`` when the reaction was successfully set. Returns ``False``
    when the reaction was skipped (disallowed by chat / known transient
    Telegram error). Never raises.
    """
    allowed = await _fetch_allowed(bot, chat_id)
    if not isinstance(allowed, _Unknown) and not _reaction_allowed(reaction, allowed):
        logger.warning(
            f"reaction skipped ({log_hint}): {_describe(reaction)} not allowed "
            f"in chat — bot_id={bot.id}, chat_id={chat_id}, message_id={message_id}"
        )
        return False

    try:
        await bot.set_message_reaction(
            chat_id=chat_id, message_id=message_id, reaction=[reaction]
        )
        return True
    except TelegramBadRequest as ex:
        msg = str(ex)
        if any(fragment in msg for fragment in _SILENT_BADREQUEST_FRAGMENTS):
            return False
        if _REACTION_INVALID_FRAGMENT in msg:
            invalidate(bot.id, chat_id)
            logger.warning(
                f"set_message_reaction rejected as REACTION_INVALID "
                f"({log_hint}) — bot_id={bot.id}, chat_id={chat_id}, "
                f"message_id={message_id}, {_describe(reaction)}"
            )
            return False
        logger.error(
            f"set_message_reaction failed ({log_hint}) — bot_id={bot.id}, "
            f"chat_id={chat_id}, message_id={message_id}, "
            f"{_describe(reaction)}: {ex}"
        )
        return False
    except Exception as ex:
        logger.error(
            f"set_message_reaction raised ({log_hint}) — bot_id={bot.id}, "
            f"chat_id={chat_id}, message_id={message_id}, "
            f"{_describe(reaction)}: {ex}"
        )
        return False


async def safe_react_to_message(
    message: Message,
    reaction: ReactionTypeUnion,
    *,
    log_hint: str,
) -> bool:
    """``Message.react``-style convenience wrapper around
    :func:`safe_set_message_reaction`.
    """
    if message.bot is None:
        logger.error(f"safe_react_to_message called on detached message ({log_hint})")
        return False
    return await safe_set_message_reaction(
        message.bot,
        chat_id=message.chat.id,
        message_id=message.message_id,
        reaction=reaction,
        log_hint=log_hint,
    )

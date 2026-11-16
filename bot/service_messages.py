"""Registry of bot-authored messages posted to master chats.

Operators reply to technical bot messages ("Тикет N закрыт", "/tickets" output,
error notices) exactly as they reply to forwarded user messages. Both kinds of
target are authored by the bot, so the core cannot tell them apart by
`reply_to_message.from_user`; the `t_messages` history lookup (`resend_id`) is
the only discriminator, and for technical messages it has no row — which made
the core print an unhelpful "history lost, use /send" notice.

This module records every message the bot posts to a master chat on its own
initiative. Delivered copies of user messages are skipped: they already live in
`t_messages`, and their absence there is exactly what the notice reports.

Registration happens in an aiogram session middleware — the single choke point
shared by the core, customizations and any future plugin — so plugins need no
changes to be covered.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any

from aiogram import Bot
from aiogram import methods as aiogram_methods
from aiogram.client.session.middlewares.base import (
    NextRequestMiddlewareType,
    RequestMiddlewareType,
)
from aiogram.methods import TelegramMethod
from aiogram.types import Message, MessageId
from loguru import logger

from config.bot_config import bot_config

# Set while a user-message copy is being delivered to a master chat. Such
# messages are tracked in `t_messages` and must not enter the service registry.
_delivery_in_progress: ContextVar[bool] = ContextVar(
    "supportbots_delivery_in_progress", default=False
)

# Edit methods return the *edited* message, whose id already exists and must
# never be registered as a new bot message. Collected from the installed
# aiogram instead of a manual list, so new ``EditMessage*`` methods are covered.
_EDIT_METHODS: tuple[type, ...] = tuple(
    obj
    for name, obj in vars(aiogram_methods).items()
    if isinstance(obj, type)
    and (name.startswith("EditMessage") or name == "StopMessageLiveLocation")
)


def delivery_in_progress() -> bool:
    """True while a copy of a user message is being delivered."""
    return _delivery_in_progress.get()


def suppress_registration(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Mark a delivery coroutine so its outgoing messages are not registered."""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _delivery_in_progress.set(True)
        try:
            return await func(*args, **kwargs)
        finally:
            _delivery_in_progress.reset(token)

    return wrapper


@contextmanager
def registration_enabled() -> Iterator[None]:
    """Register bot notices sent from inside a delivery error path.

    Delivery coroutines are wrapped in `suppress_registration`, but their
    failure notices ("Ошибка отправки") are ordinary service messages in the
    master chat: an operator replies to them and expects silence.
    """
    token = _delivery_in_progress.set(False)
    try:
        yield
    finally:
        _delivery_in_progress.reset(token)


def _normalize_chat_id(raw: Any) -> int | None:
    """Return a numeric chat id; string handles ("@channel") are not tracked."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _extract_message_ids(result: Any) -> list[int]:
    """Message ids produced by a Telegram method result (albums aware).

    `Send*`/`Forward*` return `Message`, `Copy*` returns `MessageId`, and the
    plural variants return lists of `MessageId`.
    """
    if isinstance(result, (Message, MessageId)):
        return [result.message_id]
    if isinstance(result, (list, tuple)):
        return [
            item.message_id for item in result if isinstance(item, (Message, MessageId))
        ]
    return []


def is_master_chat(bot_id: int, chat_id: int) -> bool:
    """True when `chat_id` is the configured master chat of this bot."""
    settings = bot_config.get_bot_setting(bot_id)
    master_chat = getattr(settings, "master_chat", None)
    return master_chat is not None and int(master_chat) == chat_id


async def register_bot_message(bot_id: int, chat_id: int, message_id: int) -> None:
    """Persist one bot-authored message in the service registry."""
    # Imported lazily: database.models builds the engine at import time.
    from database.models import save_service_message

    await save_service_message(bot_id=bot_id, chat_id=chat_id, message_id=message_id)


class ServiceMessageRecorder:
    """Session middleware recording bot-authored messages in master chats."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType,
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        result = await make_request(bot, method)
        try:
            await self._record(bot, method, result)
        except Exception:  # never break outbound API calls
            logger.exception(
                f"service message registry failed — bot_id={bot.id}, "
                f"method={type(method).__name__}"
            )
        return result

    async def _record(self, bot: Bot, method: TelegramMethod[Any], result: Any) -> None:
        if delivery_in_progress():
            return
        if isinstance(method, _EDIT_METHODS):
            return
        chat_id = _normalize_chat_id(getattr(method, "chat_id", None))
        if chat_id is None:
            return
        if not is_master_chat(bot.id, chat_id):
            return
        message_ids = _extract_message_ids(result)
        if not message_ids:
            return
        for message_id in message_ids:
            await register_bot_message(bot.id, chat_id, message_id)


def install_recorder(bot: Bot) -> None:
    """Attach the recorder to a bot session exactly once."""
    recorder: RequestMiddlewareType = ServiceMessageRecorder()
    if any(isinstance(m, ServiceMessageRecorder) for m in bot.session.middleware):
        return
    bot.session.middleware(recorder)

import asyncio
import json
import re
import time
from dataclasses import dataclass
from html import escape
from typing import Any, cast
from urllib.parse import quote, unquote

import redis.asyncio as aioredis
from aiogram import Bot, F, Router, types
from aiogram.enums import ChatMemberStatus
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, Message
from loguru import logger

from .interface import AbstractBotCustomization
from .registry import register_customization
from config.bot_config import SupportBotSettings, bot_config
from database.repositories import Repo


HELPER_EVENTS_CHAT_ID = -1002263825546
HELPER_BOT_ID = 5173438724
ACK_TIMEOUT_SECONDS = 300
# /ticket_close_<id> is an emergency command: chat admins only, and the close
# itself needs a confirmation that stays valid for two minutes.
CLOSE_CONFIRM_TTL_SECONDS = 120
CLOSE_ADMIN_ONLY_TEXT = "Закрывать тикеты может только админ чата"
CLOSE_MASTER_CHAT_ONLY_TEXT = "Закрытие тикетов доступно только в мастер-чате"
# Non-breaking space keeps /ticket_close_<id> on the same line as "закрыть".
NBSP = "\u00a0"

_CLAIM_TICKET_SCRIPT = """
local claimed = redis.call('SET', KEYS[1], ARGV[1], 'NX')
if claimed then
    redis.call('SADD', KEYS[2], ARGV[2])
    return 1
end
return 0
"""

_redis_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(bot_config.REDIS_URL, decode_responses=True)
    return _redis_client


def _legacy_assignment_key(bot_id: int, user_id: int) -> str:
    return f"helper:assign:{bot_id}:{user_id}"


def _ticket_assignment_key(bot_id: int, user_id: int, ticket_msg_id: int) -> str:
    return f"helper:assign:{bot_id}:{user_id}:{ticket_msg_id}"


def _user_tickets_key(bot_id: int, user_id: int) -> str:
    return f"helper:user_tickets:{bot_id}:{user_id}"


def _parse_assignment_key(key: str) -> tuple[int, int | None] | None:
    """Return (user_id, ticket_msg_id or None) for legacy/ticket keys."""
    parts = key.split(":")
    if len(parts) not in (4, 5) or parts[:2] != ["helper", "assign"]:
        return None
    try:
        int(parts[2])
        user_id = int(parts[3])
        ticket_msg_id = int(parts[4]) if len(parts) == 5 else None
    except ValueError:
        return None
    return user_id, ticket_msg_id


@dataclass
class Assignment:
    agent: str
    ticket_msg: int
    user_id: int | None = None


def _parse_assignment(
    raw: str | None, *, user_id: int | None = None
) -> Assignment | None:
    """Parse a stored assignment; corrupted entries degrade to None."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return Assignment(
            agent=str(data.get("agent") or ""),
            ticket_msg=int(data["ticket_msg"]),
            user_id=user_id,
        )
    except Exception as ex:
        logger.warning(f"helper assignment parse failed for {raw!r}: {ex}")
        return None


async def _read_assignment(
    key: str, *, user_id: int, raise_on_error: bool = False
) -> Assignment | None:
    try:
        raw = await _get_redis().get(key)
    except Exception as ex:
        logger.warning(f"helper assignment read failed for {key!r}: {ex}")
        if raise_on_error:
            raise
        return None
    return _parse_assignment(raw, user_id=user_id)


async def _get_legacy_assignment(
    *, bot_id: int, user_id: int, raise_on_error: bool = False
) -> Assignment | None:
    return await _read_assignment(
        _legacy_assignment_key(bot_id, user_id),
        user_id=user_id,
        raise_on_error=raise_on_error,
    )


async def _get_ticket_assignment(
    *,
    bot_id: int,
    user_id: int,
    ticket_msg_id: int,
    raise_on_error: bool = False,
) -> Assignment | None:
    assignment = await _read_assignment(
        _ticket_assignment_key(bot_id, user_id, ticket_msg_id),
        user_id=user_id,
        raise_on_error=raise_on_error,
    )
    if assignment is not None and assignment.ticket_msg == ticket_msg_id:
        return assignment
    legacy = await _get_legacy_assignment(
        bot_id=bot_id, user_id=user_id, raise_on_error=raise_on_error
    )
    if legacy is not None and legacy.ticket_msg == ticket_msg_id:
        return legacy
    return None


async def _get_assignments_for_user(*, bot_id: int, user_id: int) -> list[Assignment]:
    """Return all open ticket assignments of a user, oldest ticket first."""
    legacy_key = _legacy_assignment_key(bot_id, user_id)
    index_key = _user_tickets_key(bot_id, user_id)
    try:
        redis = cast(Any, _get_redis())
        raw_ticket_ids = await redis.smembers(index_key)
        ticket_ids: list[int] = []
        corrupted_ids: list[str] = []
        for raw in raw_ticket_ids:
            try:
                ticket_ids.append(int(raw))
            except (TypeError, ValueError):
                logger.warning(f"helper ticket index value skipped: {raw!r}")
                corrupted_ids.append(str(raw))
        keys = [legacy_key]
        keys.extend(
            _ticket_assignment_key(bot_id, user_id, ticket) for ticket in ticket_ids
        )
        raws = await redis.mget(keys)
    except Exception as ex:
        logger.warning(
            f"helper assignment read failed — bot_id={bot_id}, user_id={user_id}: {ex}"
        )
        return []

    by_ticket: dict[int, Assignment] = {}
    stale_ids: list[str] = []
    for key, raw in zip(keys, raws, strict=True):
        parsed_key = _parse_assignment_key(key)
        assignment = _parse_assignment(raw, user_id=user_id)
        if assignment is None:
            if parsed_key is not None and parsed_key[1] is not None:
                stale_ids.append(str(parsed_key[1]))
            continue
        if parsed_key is not None and parsed_key[1] is not None:
            assignment.ticket_msg = parsed_key[1]
        by_ticket[assignment.ticket_msg] = assignment
    if stale_ids or corrupted_ids:
        try:
            await redis.srem(index_key, *(stale_ids + corrupted_ids))
        except Exception as ex:
            logger.warning(
                f"helper ticket index cleanup failed — bot_id={bot_id}, "
                f"user_id={user_id}: {ex}"
            )
    return [by_ticket[ticket] for ticket in sorted(by_ticket)]


async def _scan_assignments(bot_id: int) -> list[tuple[str, Assignment]]:
    """Read every stored assignment of a bot; corrupted entries are skipped.
    SCAN may return the same key more than once, hence the dedup."""
    keys = {
        key
        async for key in _get_redis().scan_iter(
            match=f"helper:assign:{bot_id}:*", count=100
        )
    }
    if not keys:
        return []
    ordered_keys = sorted(keys)
    raws: list[str | None] = []
    for start in range(0, len(ordered_keys), 200):
        batch = ordered_keys[start : start + 200]
        raws.extend(await _get_redis().mget(batch))

    by_ticket: dict[tuple[int, int], tuple[str, Assignment]] = {}
    for key, raw in zip(ordered_keys, raws, strict=True):
        parsed_key = _parse_assignment_key(key)
        if parsed_key is None:
            logger.warning(f"helper assignment key skipped: {key!r}")
            continue
        user_id, ticket_from_key = parsed_key
        assignment = _parse_assignment(raw, user_id=user_id)
        if assignment is None:
            continue
        if ticket_from_key is not None:
            if ticket_from_key != assignment.ticket_msg:
                logger.warning(
                    f"helper assignment key/value mismatch for {key!r}: "
                    f"key={ticket_from_key}, value={assignment.ticket_msg}"
                )
            assignment.ticket_msg = ticket_from_key
        by_ticket[(user_id, assignment.ticket_msg)] = (key, assignment)
    return list(by_ticket.values())


async def _find_assignment_by_ticket(
    bot_id: int, ticket_msg_id: int
) -> tuple[str, Assignment] | None:
    """Find a ticket by id. New keys encode the id, so the common path is a
    targeted SCAN; the legacy per-user key still needs a full fallback scan."""
    redis = _get_redis()
    exact_pattern = f"helper:assign:{bot_id}:*:{ticket_msg_id}"
    async for key in redis.scan_iter(match=exact_pattern, count=100):
        parsed_key = _parse_assignment_key(key)
        if parsed_key is None or parsed_key[1] != ticket_msg_id:
            continue
        raw = await redis.get(key)
        assignment = _parse_assignment(raw, user_id=parsed_key[0])
        if assignment is not None:
            return key, assignment

    for key, assignment in await _scan_assignments(bot_id):
        if assignment.ticket_msg == ticket_msg_id:
            return key, assignment
    return None


async def _claim_ticket(
    *,
    bot_id: int,
    user_id: int,
    agent_username: str,
    ticket_msg_id: int,
) -> bool:
    """Atomically claim one ticket. A legacy assignment for the same ticket
    also blocks the claim; Redis errors propagate to the caller."""
    legacy = await _get_legacy_assignment(
        bot_id=bot_id, user_id=user_id, raise_on_error=True
    )
    if legacy is not None and legacy.ticket_msg == ticket_msg_id:
        return False
    payload = json.dumps({"agent": agent_username, "ticket_msg": ticket_msg_id})
    redis = cast(Any, _get_redis())
    result = await redis.eval(
        _CLAIM_TICKET_SCRIPT,
        2,
        _ticket_assignment_key(bot_id, user_id, ticket_msg_id),
        _user_tickets_key(bot_id, user_id),
        payload,
        str(ticket_msg_id),
    )
    return bool(result)


async def _restore_ticket_assignment(
    *, bot_id: int, user_id: int, ticket_msg_id: int, agent_username: str
) -> None:
    try:
        payload = json.dumps({"agent": agent_username, "ticket_msg": ticket_msg_id})
        redis = cast(Any, _get_redis())
        pipe = redis.pipeline(transaction=True)
        pipe.set(
            _ticket_assignment_key(bot_id, user_id, ticket_msg_id),
            payload,
            nx=True,
        )
        pipe.sadd(_user_tickets_key(bot_id, user_id), str(ticket_msg_id))
        await pipe.execute()
    except Exception as ex:
        logger.warning(
            f"helper assignment restore failed — bot_id={bot_id}, "
            f"user_id={user_id}, ticket={ticket_msg_id}: {ex}"
        )


async def _delete_ticket_assignment(
    *, bot_id: int, user_id: int, ticket_msg_id: int
) -> bool | None:
    try:
        redis = cast(Any, _get_redis())
        keys = [_ticket_assignment_key(bot_id, user_id, ticket_msg_id)]
        legacy = await _get_legacy_assignment(
            bot_id=bot_id, user_id=user_id, raise_on_error=True
        )
        if legacy is not None and legacy.ticket_msg == ticket_msg_id:
            keys.append(_legacy_assignment_key(bot_id, user_id))
        pipe = redis.pipeline(transaction=True)
        pipe.delete(*keys)
        results = await pipe.execute()
        deleted = results[0]
        try:
            await redis.srem(_user_tickets_key(bot_id, user_id), str(ticket_msg_id))
        except Exception as ex:
            logger.warning(
                f"helper ticket index cleanup failed — bot_id={bot_id}, "
                f"user_id={user_id}, ticket={ticket_msg_id}: {ex}"
            )
        return bool(deleted)
    except Exception as ex:
        logger.warning(
            f"helper assignment delete failed — bot_id={bot_id}, "
            f"user_id={user_id}, ticket={ticket_msg_id}: {ex}"
        )
        return None


def _format_agent_tag(agent: str) -> str:
    """Render an agent reference for HTML-parse-mode texts."""
    clean = escape(agent.lstrip("@"))
    return f"@{clean}" if clean else "-"


def _plain_agent_tag(agent: str) -> str:
    """Render an agent reference for plain-text callback alerts."""
    clean = agent.lstrip("@")
    return f"@{clean}" if clean else "-"


def _foreign_command_mention(text: str, bot_username: str | None) -> bool:
    """True when a command text is addressed to a different bot via the
    Telegram `@bot_username` suffix."""
    tokens = text.split()
    if not tokens:
        return False
    first = tokens[0]
    if "@" not in first:
        return False
    mention = first.split("@", 1)[1]
    clean_bot = bot_username.lstrip("@").lower() if bot_username else None
    return not clean_bot or mention.lower() != clean_bot


@dataclass
class PendingAck:
    op: str
    url: str
    master_chat_id: int
    master_thread_id: int | None
    agent_username: str


def _encode_value(value: str) -> str:
    return quote(value, safe="")


def _is_valid_url(url: str | None) -> bool:
    return bool(url) and (url.startswith("https://") or url.startswith("http://"))


def _message_url(chat: types.Chat, message_id: int) -> str | None:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    if str(chat.id).startswith("-100"):
        internal_id = str(chat.id).removeprefix("-100")
        return f"https://t.me/c/{internal_id}/{message_id}"
    return None


def _extract_message_url(message: object) -> str:
    chat = getattr(message, "chat", None)
    message_id = getattr(message, "message_id", None)
    if chat is not None and isinstance(message_id, int):
        return _message_url(chat, message_id) or ""
    return ""


def _extract_text_or_caption(message: Message) -> str:
    return message.text or message.caption or ""


def _parse_helper_channel_message(text: str) -> dict[str, str] | None:
    tokens = text.strip().split()
    if not tokens or tokens[0] != "#helper":
        return None

    result: dict[str, str] = {}
    for token in tokens[1:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key:
            result[key] = unquote(value)
    return result


def _pending_key(op: str, url: str) -> tuple[str, str]:
    return op, url


def _should_alert_on_error_reason(reason: str) -> bool:
    return reason in {"missing_url", "invalid_payload", "processing_failed"}


def _build_taken_message(
    user_id: int, username: str, agent_username: str, url: str
) -> str:
    normalized_username = username if username else "-"
    return (
        "#skynet #helper "
        f"command=taken "
        f"user_id={user_id} "
        f"username={_encode_value(normalized_username)} "
        f"agent_username={_encode_value(agent_username.lstrip('@'))} "
        f"url={_encode_value(url)}"
    )


def _build_closed_message(user_id: int, agent_username: str, url: str) -> str:
    return (
        "#skynet #helper "
        f"command=closed "
        f"user_id={user_id} "
        f"agent_username={_encode_value(agent_username.lstrip('@'))} "
        f"url={_encode_value(url)} "
        "closed=true"
    )


class GetCallbackData(CallbackData, prefix="get"):
    user_id: int
    username: str


class EndCallbackData(CallbackData, prefix="end"):
    ticket_user_id: int
    user_id: int
    username: str


class CloseTicketCallbackData(CallbackData, prefix="cl"):
    """Close confirmation: ticket, client, invoker and issue time."""

    ticket_msg_id: int
    client_id: int
    user_id: int
    issued_at: int


class CancelCloseCallbackData(CallbackData, prefix="clx"):
    ticket_msg_id: int


def _master_chat_settings(
    bot_id: int,
) -> tuple[SupportBotSettings, int] | None:
    """Bot settings with the resolved master chat id, when configured."""
    settings = bot_config.get_bot_setting(bot_id)
    if settings is None or settings.master_chat is None:
        return None
    return settings, int(settings.master_chat)


async def _is_chat_admin(bot: Bot, chat_id: int, user_id: int | None) -> bool:
    """True for chat creator/administrator; Telegram failures deny access."""
    if user_id is None:
        return False
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception as ex:
        logger.warning(
            f"helper admin check failed — chat_id={chat_id}, user_id={user_id}: {ex}"
        )
        return False
    return member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)


def _closed_reply_markup(
    ticket_user_id: int, agent_username: str
) -> InlineKeyboardMarkup:
    agent_username = agent_username.lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"Закрыл {agent_username}",
                    callback_data=EndCallbackData(
                        ticket_user_id=ticket_user_id,
                        user_id=0,
                        username=agent_username,
                    ).pack(),
                )
            ]
        ]
    )


def _is_closed_ticket_message(message: object) -> bool:
    markup = getattr(message, "reply_markup", None)
    if not isinstance(markup, InlineKeyboardMarkup):
        return False
    for row in markup.inline_keyboard:
        for button in row:
            if button.text and button.text.startswith("Закрыл "):
                return True
            callback_data = button.callback_data
            if not callback_data or not callback_data.startswith("end:"):
                continue
            parts = callback_data.split(":")
            if len(parts) >= 3 and parts[2] == "0":
                return True
    return False


_PENDING_ACKS: dict[tuple[str, str], PendingAck] = {}
_PENDING_TASKS: dict[tuple[str, str], asyncio.Task[None]] = {}


@register_customization(bot_id=HELPER_BOT_ID)
class HelperCustomization(AbstractBotCustomization):
    # Shared across the short-lived instances created by get_customization():
    # channel-post/button handlers are bound to the router instance, while
    # /take runs on a fresh instance from the core hook.
    _pending_acks = _PENDING_ACKS
    _pending_tasks = _PENDING_TASKS

    def __init__(self):
        self._router: Router | None = None

    @property
    def router(self) -> Router:
        # Built lazily: get_customization() creates a fresh instance per
        # message, and only the loader ever needs the router.
        if self._router is None:
            self._router = self._build_router()
        return self._router

    def _build_router(self) -> Router:
        # Customization routers receive updates from every support bot; scope
        # this plugin's handlers to the helper bot (a failed router filter
        # skips the router and propagation continues to other handlers).
        # Master-chat commands (/tickets, /ticket_close_{id}) are NOT router
        # handlers: the core catch-all message handler would shadow them.
        # They are dispatched through the handle_master_message hook instead.
        router = Router()
        router.channel_post.filter(
            F.bot.id == HELPER_BOT_ID, F.chat.id == HELPER_EVENTS_CHAT_ID
        )
        router.callback_query.filter(F.bot.id == HELPER_BOT_ID)
        router.callback_query.register(
            self.callbacks_lang_get, GetCallbackData.filter()
        )
        router.callback_query.register(
            self.callbacks_lang_end, EndCallbackData.filter()
        )
        router.callback_query.register(
            self.callbacks_ticket_close, CloseTicketCallbackData.filter()
        )
        router.callback_query.register(
            self.callbacks_ticket_close_cancel, CancelCloseCallbackData.filter()
        )
        router.channel_post.register(
            self.handle_helper_channel_post, F.text.regexp(r"^\s*#helper\b")
        )
        router.channel_post.register(
            self.handle_helper_channel_post, F.caption.regexp(r"^\s*#helper\b")
        )
        return router

    def _register_pending_ack(
        self,
        op: str,
        url: str,
        master_chat_id: int,
        master_thread_id: int | None,
        agent_username: str,
        bot: Bot,
    ):
        key = _pending_key(op, url)
        previous_task = self._pending_tasks.pop(key, None)
        if previous_task is not None:
            previous_task.cancel()

        self._pending_acks[key] = PendingAck(
            op=op,
            url=url,
            master_chat_id=master_chat_id,
            master_thread_id=master_thread_id,
            agent_username=agent_username,
        )
        self._pending_tasks[key] = asyncio.create_task(
            self._ack_timeout_worker(key, bot)
        )

    def _resolve_pending_ack(self, op: str, url: str) -> PendingAck | None:
        key = _pending_key(op, url)
        pending = self._pending_acks.pop(key, None)
        task = self._pending_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        return pending

    async def _ack_timeout_worker(self, key: tuple[str, str], bot: Bot):
        try:
            await asyncio.sleep(ACK_TIMEOUT_SECONDS)
            pending = self._pending_acks.get(key)
            if pending is None:
                return

            logger.warning(
                "Helper ACK timeout op={} url={} chat_id={}",
                pending.op,
                pending.url,
                pending.master_chat_id,
            )
            await bot.send_message(
                chat_id=pending.master_chat_id,
                message_thread_id=pending.master_thread_id,
                text=(
                    "Не пришло подтверждение из helper-канала за 5 минут. "
                    f"op={pending.op} url={pending.url}"
                ),
            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Failed to notify helper ACK timeout")
        finally:
            current = asyncio.current_task()
            if self._pending_tasks.get(key) is current:
                self._pending_acks.pop(key, None)
                self._pending_tasks.pop(key, None)

    async def _notify_pending(self, bot: Bot, pending: PendingAck, text: str):
        await bot.send_message(
            chat_id=pending.master_chat_id,
            message_thread_id=pending.master_thread_id,
            text=text,
        )

    async def _notify_pending_by_error(self, bot: Bot, reason: str, op: str, url: str):
        if url and op:
            pending = self._resolve_pending_ack(op, url)
            if pending is None:
                return
            await self._notify_pending(
                bot,
                pending,
                f"Ошибка helper ACK: reason={reason} op={op} url={url}",
            )
            return

        if url:
            matched = [k for k in self._pending_acks if k[1] == url]
            for key in matched:
                pending = self._resolve_pending_ack(key[0], key[1])
                if pending is None:
                    continue
                await self._notify_pending(
                    bot,
                    pending,
                    f"Ошибка helper ACK: reason={reason} op={key[0]} url={url}",
                )
            return

        uniq_targets: set[tuple[int, int | None]] = set()
        for pending in self._pending_acks.values():
            uniq_targets.add((pending.master_chat_id, pending.master_thread_id))
        for chat_id, thread_id in uniq_targets:
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=f"Ошибка helper ACK: reason={reason}",
            )
        for key in list(self._pending_acks):
            self._resolve_pending_ack(key[0], key[1])

    async def handle_helper_channel_post(self, message: Message):
        text = _extract_text_or_caption(message)
        if not text:
            return

        payload = _parse_helper_channel_message(text)
        if payload is None:
            return

        command = payload.get("command", "")
        op = payload.get("op", "")
        url = payload.get("url", "")

        if command == "ack":
            status = payload.get("status", "")
            if status not in {"ok", "duplicate"}:
                logger.warning("Unknown helper ack status: {}", status)
                return
            pending = self._resolve_pending_ack(op, url)
            if pending is None:
                logger.info(
                    "Helper ACK without pending op={} url={} status={}", op, url, status
                )
                return
            logger.info("Helper ACK received op={} url={} status={}", op, url, status)
            return

        if command == "error":
            reason = payload.get("reason", "")
            logger.warning(
                "Helper error received reason={} op={} url={}", reason, op, url
            )
            if not _should_alert_on_error_reason(reason):
                return
            bot = message.bot
            if bot is None:
                logger.warning("Helper error cannot notify pending: bot is None")
                return
            await self._notify_pending_by_error(cast(Bot, bot), reason, op, url)
            return

        logger.info("Ignored helper channel command={}", command)

    async def handle_master_message(
        self,
        message: Message,
        bot_settings: SupportBotSettings,
        *,
        bot: Bot | None = None,
        repo: Repo | None = None,
    ) -> bool:
        """Core hook: /take, /tickets and /ticket_close_{id} in master chat."""
        text = _extract_text_or_caption(message).strip()
        is_take = bool(re.match(r"^/take(?:[\s@_]|$)", text, re.IGNORECASE))
        is_tickets = bool(re.match(r"^/tickets(?:[\s@]|$)", text, re.IGNORECASE))
        is_close = bool(re.match(r"^/ticket_close(?:[\s@_]|$)", text, re.IGNORECASE))
        if not is_take and not is_tickets and not is_close:
            return False
        if _foreign_command_mention(text, bot_settings.username):
            # Never let a command-like reply fall through to the client when
            # it is addressed to another bot or has a typo in our username.
            return True
        try:
            if is_take:
                await self.cmd_take(message, bot_settings, bot=bot, repo=repo)
            elif is_tickets:
                await self.cmd_tickets(message, bot_settings)
            else:
                await self.cmd_ticket_close(message, bot_settings, bot=bot, repo=repo)
        except Exception:
            # A recognized master-chat command must never fall through to the
            # core: for a reply to a ticket the core would send the command
            # text to the client as an agent reply.
            logger.exception(
                "helper master command failed — bot_id={}, text={!r}",
                bot_settings.id,
                text,
            )
            try:
                await message.answer("Не удалось обработать команду")
            except Exception:
                logger.exception("helper command failure reply failed")
        return True

    async def _install_take_close_button(
        self,
        *,
        bot: Bot,
        ticket_message: Message,
        client_user_id: int,
        agent_user_id: int,
        agent_username: str,
    ) -> None:
        """Put the same close button on the ticket that the button flow uses."""
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=f"Взял {agent_username}",
                        callback_data=EndCallbackData(
                            ticket_user_id=client_user_id,
                            user_id=agent_user_id,
                            username=agent_username,
                        ).pack(),
                    )
                ]
            ]
        )
        try:
            await bot.edit_message_reply_markup(
                chat_id=ticket_message.chat.id,
                message_id=ticket_message.message_id,
                reply_markup=reply_markup,
            )
        except Exception as ex:
            logger.warning(
                "helper close button install failed — bot_id={}, ticket_msg_id={}: {}",
                bot.id,
                ticket_message.message_id,
                ex,
            )

    async def cmd_take(
        self,
        message: Message,
        bot_settings: SupportBotSettings,
        *,
        bot: Bot | None = None,
        repo: Repo | None = None,
    ) -> None:
        bot = bot or message.bot
        if bot is None or repo is None:
            logger.warning(
                "helper /take missing dependencies — bot={}, repo={}",
                bot is not None,
                repo is not None,
            )
            await message.answer(
                "Не удалось обработать /take: внутренняя зависимость недоступна"
            )
            return

        if message.text is None:
            await message.answer(
                "Команда /take не поддерживает вложения. Отправьте её текстом."
            )
            return

        tokens = (message.text or "").split()
        if not tokens:
            return
        command = tokens[0].lower()
        if command.startswith("/take_"):
            await message.answer(
                "Команда /take используется ответом на сообщение тикета, без ID."
            )
            return

        if len(tokens) > 1:
            await message.answer(
                "Команда /take не принимает аргументов. Ответьте ею на сообщение тикета."
            )
            return

        target = message.reply_to_message
        if target is None:
            await message.answer(
                "Команду /take нужно отправить ответом на сообщение тикета"
            )
            return
        if target.text is None:
            await message.answer(
                "Ответьте /take на текстовое сообщение с ID тикета "
                "(под медиа-вложением), а не на само медиа"
            )
            return
        if _is_closed_ticket_message(target):
            await message.answer(f"Тикет {target.message_id} уже закрыт")
            return

        from_user = message.from_user
        if from_user is None or from_user.is_bot or not from_user.username:
            await message.answer("Установите @username в Telegram, чтобы брать тикеты")
            return
        agent_username = from_user.username

        try:
            resend_info = await repo.get_message_resend_info(
                bot_id=bot_settings.id,
                resend_id=target.message_id,
                chat_for_id=target.chat.id,
            )
        except Exception:
            logger.exception(
                "helper /take ticket lookup failed — bot_id={}, ticket_msg_id={}",
                bot_settings.id,
                target.message_id,
            )
            await message.answer("Не удалось взять тикет: ошибка базы")
            return
        if resend_info is None:
            await message.answer(
                "Это сообщение не является тикетом пользователя. "
                "Ответьте на пересланное сообщение из тикета."
            )
            return
        client_user_id = int(resend_info.chat_from_id)

        url = _extract_message_url(target)
        if not _is_valid_url(url):
            await message.answer(
                "Не удалось отправить событие: некорректный URL тикета"
            )
            return

        try:
            claimed = await _claim_ticket(
                bot_id=bot_settings.id,
                user_id=client_user_id,
                agent_username=agent_username,
                ticket_msg_id=target.message_id,
            )
        except Exception as ex:
            logger.warning(f"helper /take claim failed: {ex}")
            await message.answer("Не удалось закрепить задачу, Redis недоступен")
            return
        if not claimed:
            try:
                current = await _get_ticket_assignment(
                    bot_id=bot_settings.id,
                    user_id=client_user_id,
                    ticket_msg_id=target.message_id,
                    raise_on_error=True,
                )
            except Exception:
                logger.exception("helper /take assignment read failed")
                await message.answer("Не удалось закрепить задачу, Redis недоступен")
                return
            if (
                current is not None
                and current.agent.lstrip("@").lower()
                == agent_username.lstrip("@").lower()
            ):
                await self._install_take_close_button(
                    bot=bot,
                    ticket_message=target,
                    client_user_id=client_user_id,
                    agent_user_id=from_user.id,
                    agent_username=agent_username,
                )
                await message.answer(f"Тикет {target.message_id} уже закреплён за вами")
            else:
                if current is not None:
                    await message.answer(
                        f"Тикет {target.message_id} уже взят "
                        f"{_format_agent_tag(current.agent)}",
                        parse_mode="HTML",
                    )
                else:
                    await message.answer(
                        f"Тикет {target.message_id} уже взят другим агентом"
                    )
            return

        try:
            client_chat = await asyncio.wait_for(
                bot.get_chat(client_user_id), timeout=2.0
            )
            client_username = client_chat.username or "-"
        except Exception:
            client_username = "-"

        event_message = _build_taken_message(
            user_id=client_user_id,
            username=client_username,
            agent_username=agent_username,
            url=url,
        )
        try:
            await bot.send_message(chat_id=HELPER_EVENTS_CHAT_ID, text=event_message)
        except Exception:
            await _delete_ticket_assignment(
                bot_id=bot_settings.id,
                user_id=client_user_id,
                ticket_msg_id=target.message_id,
            )
            logger.exception("helper /take event failed — assignment rolled back")
            await message.answer("Не удалось закрепить задачу: ошибка отправки события")
            return

        self._register_pending_ack(
            op="taken",
            url=url,
            master_chat_id=target.chat.id,
            master_thread_id=(
                message.message_thread_id
                or target.message_thread_id
                or bot_settings.master_thread
            ),
            agent_username=agent_username,
            bot=bot,
        )
        await self._install_take_close_button(
            bot=bot,
            ticket_message=target,
            client_user_id=client_user_id,
            agent_user_id=from_user.id,
            agent_username=agent_username,
        )
        await message.answer(
            f"Тикет {target.message_id} взят {_format_agent_tag(agent_username)}. "
            f"Закрыть: /ticket_close_{target.message_id}",
            parse_mode="HTML",
        )

    async def cmd_tickets(
        self, message: Message, bot_settings: SupportBotSettings
    ) -> None:
        try:
            stored = await _scan_assignments(bot_settings.id)
        except Exception as ex:
            logger.warning(f"helper /tickets failed: {ex}")
            await message.answer(
                "Не удалось получить список тикетов, Redis недоступен",
            )
            return
        # t.me/c/ links exist only for supergroup/channel ids (-100...);
        # anything else degrades to a plain message id without a link.
        link_ok = str(message.chat.id).startswith("-100")
        internal_chat_id = str(message.chat.id).removeprefix("-100")
        rows: list[tuple[int, str]] = []
        for _key, assignment in stored:
            client_id = assignment.user_id
            if client_id is None:
                logger.warning(
                    f"helper assignment without user id skipped: {assignment!r}"
                )
                continue
            ticket = str(assignment.ticket_msg)
            if link_ok:
                link = (
                    f'<a href="https://t.me/c/{internal_chat_id}/{ticket}">{ticket}</a>'
                )
            else:
                link = ticket
            agent = _format_agent_tag(assignment.agent)
            rows.append(
                (
                    assignment.ticket_msg,
                    f"{link} от #ID{client_id} взял {agent}"
                    f" | закрыть{NBSP}/ticket_close_{ticket}",
                )
            )
        # Oldest tickets first: message ids grow over time, so the smallest
        # id at the top shows which ticket has been open the longest.
        rows.sort(key=lambda row: row[0])
        # Telegram caps one message at 4096 chars; assignments live for weeks
        # (no TTL), so the list is sent in bounded chunks.
        chunks: list[str] = []
        current = ""
        for _, line in rows:
            # A blank line between tickets keeps the list scannable in Telegram.
            candidate = f"{current}\n\n{line}" if current else line
            if len(candidate) > 4000 and current:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)
        if not chunks:
            await message.answer(
                "Открытых тикетов нет",
            )
            return
        for index, chunk in enumerate(chunks):
            if index:
                await asyncio.sleep(0.5)  # stay clear of Telegram flood limits
            header = "Открытые тикеты:\n" if index == 0 else ""
            await message.answer(
                f"{header}{chunk}",
                parse_mode="HTML",
            )

    async def cmd_ticket_close(
        self,
        message: Message,
        bot_settings: SupportBotSettings,
        *,
        bot: Bot | None = None,
        repo: Repo | None = None,
    ) -> None:
        bot = bot or message.bot
        if bot is None:  # pragma: no cover - aiogram always binds a bot
            logger.error("helper /ticket_close called without a bot")
            return
        # The command id is printed by /tickets, so a single tap used to close
        # a ticket: emergency close is reserved for chat admins and confirmed.
        operator = message.from_user
        if operator is None or not await _is_chat_admin(
            bot, message.chat.id, operator.id
        ):
            await message.answer(CLOSE_ADMIN_ONLY_TEXT)
            return
        if message.text is None:
            await message.answer(
                "Команда /ticket_close не поддерживает вложения. Отправьте её текстом."
            )
            return
        match = re.match(
            r"^/ticket_close_(\d+)(?:@\S+)?\s*$",
            (message.text or "").strip(),
            re.IGNORECASE,
        )
        if match is None:
            await message.answer(
                "Команда закрытия используется в формате /ticket_close_<id>"
            )
            return
        ticket_msg_id = int(match.group(1))
        target = await self._resolve_close_target(
            message, bot_settings, ticket_msg_id, repo
        )
        if target is None:
            return
        client_id, assignment = target
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=f"Закрыть тикет {ticket_msg_id}",
                        callback_data=CloseTicketCallbackData(
                            ticket_msg_id=ticket_msg_id,
                            client_id=client_id,
                            user_id=operator.id,
                            issued_at=int(time.time()),
                        ).pack(),
                    ),
                    types.InlineKeyboardButton(
                        text="Отмена",
                        callback_data=CancelCloseCallbackData(
                            ticket_msg_id=ticket_msg_id
                        ).pack(),
                    ),
                ]
            ]
        )
        await message.answer(
            f"Закрыть тикет {ticket_msg_id} от #ID{client_id} "
            f"(взял {_format_agent_tag(assignment.agent)})?",
            reply_markup=markup,
            parse_mode="HTML",
        )

    async def _resolve_close_target(
        self,
        message: Message,
        bot_settings: SupportBotSettings,
        ticket_msg_id: int,
        repo: Repo | None,
    ) -> tuple[int, Assignment] | None:
        """Find the open ticket or answer why it cannot be closed.

        Returns ``None`` when the operator already got an explanation.
        """
        client_id: int | None = None
        assignment: Assignment | None = None

        if repo is not None:
            try:
                resend_info = await repo.get_message_resend_info(
                    bot_id=bot_settings.id,
                    resend_id=ticket_msg_id,
                    chat_for_id=message.chat.id,
                )
            except Exception:
                logger.exception("helper /ticket_close lookup failed")
                resend_info = None
            if resend_info is not None:
                client_id = int(resend_info.chat_from_id)

        if client_id is not None:
            try:
                assignment = await _get_ticket_assignment(
                    bot_id=bot_settings.id,
                    user_id=client_id,
                    ticket_msg_id=ticket_msg_id,
                    raise_on_error=True,
                )
            except Exception:
                logger.exception("helper /ticket_close read failed")
                await message.answer("Не удалось закрыть тикет, Redis недоступен")
                return None
        else:
            try:
                found = await _find_assignment_by_ticket(bot_settings.id, ticket_msg_id)
            except Exception as ex:
                logger.warning(f"helper /ticket_close failed: {ex}")
                await message.answer(
                    "Не удалось закрыть тикет, Redis недоступен",
                )
                return None
            if found is None:
                await message.answer(
                    f"Открытый тикет с сообщением {ticket_msg_id} не найден",
                )
                return None
            _key, assignment = found
            client_id = assignment.user_id

        if assignment is None or client_id is None:
            await message.answer(
                f"Открытый тикет с сообщением {ticket_msg_id} не найден",
            )
            return None
        return client_id, assignment

    async def callbacks_ticket_close(
        self, callback: types.CallbackQuery, callback_data: CloseTicketCallbackData
    ) -> None:
        bot = callback.bot
        prompt = callback.message
        if bot is None or prompt is None:
            await callback.answer("Не удалось обработать сообщение", show_alert=True)
            return
        prompt = cast(Message, prompt)
        master = _master_chat_settings(bot.id)
        if master is None or prompt.chat.id != master[1]:
            await callback.answer(CLOSE_MASTER_CHAT_ONLY_TEXT, show_alert=True)
            return
        settings, master_chat_id = master
        if callback.from_user.id != callback_data.user_id:
            await callback.answer("Кнопка не для вас", show_alert=True)
            return
        age = time.time() - callback_data.issued_at
        if age < 0 or age > CLOSE_CONFIRM_TTL_SECONDS:
            await callback.answer(
                "Подтверждение устарело. Повторите "
                f"/ticket_close_{callback_data.ticket_msg_id}",
                show_alert=True,
            )
            await self._close_prompt(
                prompt,
                bot,
                f"Подтверждение закрытия тикета {callback_data.ticket_msg_id} устарело",
            )
            return
        if not await _is_chat_admin(bot, master_chat_id, callback.from_user.id):
            await callback.answer(CLOSE_ADMIN_ONLY_TEXT, show_alert=True)
            return
        try:
            assignment = await _get_ticket_assignment(
                bot_id=bot.id,
                user_id=callback_data.client_id,
                ticket_msg_id=callback_data.ticket_msg_id,
                raise_on_error=True,
            )
        except Exception:
            logger.exception("helper ticket close confirm read failed")
            await callback.answer(
                "Не удалось закрыть тикет: Redis недоступен", show_alert=True
            )
            return
        if assignment is None:
            await callback.answer("Этот тикет уже закрыт")
            await self._close_prompt(
                prompt, bot, f"Тикет {callback_data.ticket_msg_id} уже закрыт"
            )
            return
        await callback.answer()
        result = await self._close_ticket(
            bot_id=bot.id,
            bot=bot,
            chat=prompt.chat,
            master_thread_id=(
                getattr(prompt, "message_thread_id", None)
                or getattr(settings, "master_thread", None)
            ),
            ticket_msg_id=callback_data.ticket_msg_id,
            client_id=callback_data.client_id,
            assignment=assignment,
            closer_username=callback.from_user.username,
        )
        await self._close_prompt(prompt, bot, result)

    async def callbacks_ticket_close_cancel(
        self, callback: types.CallbackQuery, callback_data: CancelCloseCallbackData
    ) -> None:
        bot = callback.bot
        prompt = callback.message
        if bot is None or prompt is None:
            await callback.answer("Не удалось обработать сообщение", show_alert=True)
            return
        prompt = cast(Message, prompt)
        master = _master_chat_settings(bot.id)
        if master is None or prompt.chat.id != master[1]:
            await callback.answer(CLOSE_MASTER_CHAT_ONLY_TEXT, show_alert=True)
            return
        _, master_chat_id = master
        if not await _is_chat_admin(bot, master_chat_id, callback.from_user.id):
            await callback.answer(CLOSE_ADMIN_ONLY_TEXT, show_alert=True)
            return
        await callback.answer()
        await self._close_prompt(
            prompt, bot, f"Закрытие тикета {callback_data.ticket_msg_id} отменено"
        )

    async def _close_prompt(self, prompt: Message, bot: Bot, text: str) -> None:
        """Replace the confirmation prompt with the outcome and drop buttons."""
        try:
            await bot.edit_message_text(
                chat_id=prompt.chat.id,
                message_id=prompt.message_id,
                text=text,
                reply_markup=None,
                parse_mode="HTML",
            )
        except Exception as ex:
            logger.warning(
                f"helper close prompt update failed — bot_id={bot.id}, "
                f"chat_id={prompt.chat.id}, message_id={prompt.message_id}: {ex}"
            )

    async def _close_ticket(
        self,
        *,
        bot_id: int,
        bot: Bot,
        chat: types.Chat,
        master_thread_id: int | None,
        ticket_msg_id: int,
        client_id: int,
        assignment: Assignment,
        closer_username: str | None,
    ) -> str:
        """Close an open ticket and return the human-readable outcome."""
        url = _message_url(chat, ticket_msg_id)
        if url is None or not _is_valid_url(url):
            return "Не удалось отправить событие: некорректный URL тикета"
        deleted = await _delete_ticket_assignment(
            bot_id=bot_id,
            user_id=client_id,
            ticket_msg_id=ticket_msg_id,
        )
        if deleted is None:
            return "Не удалось закрыть тикет, Redis недоступен"
        if deleted is False:
            return f"Тикет {ticket_msg_id} уже закрыт"
        closer = assignment.agent
        if closer_username:
            closer = closer_username
        event_message = _build_closed_message(
            user_id=client_id,
            agent_username=closer,
            url=url,
        )
        try:
            await bot.send_message(chat_id=HELPER_EVENTS_CHAT_ID, text=event_message)
        except Exception:
            logger.exception("helper /ticket_close event failed")
            await _restore_ticket_assignment(
                bot_id=bot_id,
                user_id=client_id,
                ticket_msg_id=ticket_msg_id,
                agent_username=assignment.agent,
            )
            return "Не удалось закрыть тикет: ошибка отправки события"
        self._register_pending_ack(
            op="closed",
            url=url,
            master_chat_id=chat.id,
            master_thread_id=master_thread_id,
            agent_username=closer,
            bot=bot,
        )
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat.id,
                message_id=ticket_msg_id,
                reply_markup=_closed_reply_markup(client_id, closer.lstrip("@")),
            )
        except Exception as ex:
            logger.warning(
                "helper /ticket_close keyboard update failed — bot_id={}, "
                "ticket_msg_id={}: {}",
                bot_id,
                ticket_msg_id,
                ex,
            )
        agent = _format_agent_tag(assignment.agent)
        return f"Тикет {ticket_msg_id} закрыт (взял {agent})"

    async def get_help_text(self, bot_settings: SupportBotSettings) -> str:
        return (
            "\n\n<b>Helper</b>\n"
            "<code>/take</code> — ответом на сообщение взять тикет без кнопки\n"
            "<code>/tickets</code> — открытые тикеты и их исполнители\n"
            "<code>/ticket_close_&lt;id&gt;</code> — закрыть тикет по ID сообщения"
        )

    async def get_extra_text(
        self, user: types.User, message: Message, bot_settings: SupportBotSettings
    ) -> str:
        extra = f"\n/get_info_{user.id}@mymtlbot"
        assignments = await _get_assignments_for_user(
            bot_id=bot_settings.id, user_id=user.id
        )
        agents: list[str] = []
        seen: set[str] = set()
        for assignment in assignments:
            if not assignment.agent:
                continue
            normalized = assignment.agent.lstrip("@").lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            agents.append(assignment.agent)
        if agents:
            extra += "\nCC " + " ".join(_format_agent_tag(agent) for agent in agents)
        return extra

    def _take_button(self, user: types.User) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Взять",
                        callback_data=GetCallbackData(
                            user_id=user.id, username=user.username or "-"
                        ).pack(),
                    )
                ]
            ]
        )

    async def get_reply_markup(
        self, user: types.User, message: Message, bot_settings: SupportBotSettings
    ) -> InlineKeyboardMarkup | None:
        assignments = await _get_assignments_for_user(
            bot_id=bot_settings.id, user_id=user.id
        )
        if assignments:
            return None
        return self._take_button(user)

    async def callbacks_lang_get(
        self, callback: types.CallbackQuery, callback_data: GetCallbackData
    ):
        message = callback.message
        bot = callback.bot
        if message is None:
            await callback.answer("Не удалось обработать сообщение", show_alert=True)
            return
        if bot is None:
            await callback.answer("Не удалось отправить событие", show_alert=True)
            return
        assert bot is not None
        message_obj = cast(Message, message)

        if not callback.from_user.username:
            # CC tagging requires a real Telegram @username; without one the
            # taker could never be notified, so the take is refused.
            await callback.answer(
                "Установите @username в Telegram, чтобы брать тикеты",
                show_alert=True,
            )
            return
        agent_username = callback.from_user.username
        url = _extract_message_url(message_obj) or ""

        if not _is_valid_url(url):
            await callback.answer(
                "Не удалось отправить событие: некорректный URL", show_alert=True
            )
            return
        if _is_closed_ticket_message(message_obj):
            await callback.answer("Тикет уже закрыт", show_alert=True)
            return

        try:
            claimed = await _claim_ticket(
                bot_id=bot.id,
                user_id=callback_data.user_id,
                agent_username=agent_username,
                ticket_msg_id=message_obj.message_id,
            )
        except Exception as ex:
            logger.warning(f"helper take claim failed: {ex}")
            await callback.answer(
                "Не удалось закрепить задачу, Redis недоступен", show_alert=True
            )
            return
        if not claimed:
            try:
                current = await _get_ticket_assignment(
                    bot_id=bot.id,
                    user_id=callback_data.user_id,
                    ticket_msg_id=message_obj.message_id,
                    raise_on_error=True,
                )
            except Exception:
                logger.exception("helper take assignment read failed")
                await callback.answer(
                    "Не удалось закрепить задачу, Redis недоступен",
                    show_alert=True,
                )
                return
            if (
                current is not None
                and current.agent.lstrip("@").lower()
                == agent_username.lstrip("@").lower()
            ):
                await self._install_take_close_button(
                    bot=bot,
                    ticket_message=message_obj,
                    client_user_id=callback_data.user_id,
                    agent_user_id=callback.from_user.id,
                    agent_username=agent_username,
                )
                await callback.answer("Тикет уже закреплён за вами")
            elif current is not None:
                await callback.answer(
                    f"Тикет уже взят {_plain_agent_tag(current.agent)}!",
                    show_alert=True,
                )
            else:
                await callback.answer("Тикет уже взят другим агентом!", show_alert=True)
            return

        event_message = _build_taken_message(
            user_id=callback_data.user_id,
            username=callback_data.username,
            agent_username=agent_username,
            url=url,
        )
        try:
            await bot.send_message(chat_id=HELPER_EVENTS_CHAT_ID, text=event_message)
        except Exception:
            # roll the claim back so the take can be retried, then tell the
            # agent instead of leaving the button spinner hanging
            await _delete_ticket_assignment(
                bot_id=bot.id,
                user_id=callback_data.user_id,
                ticket_msg_id=message_obj.message_id,
            )
            logger.exception("helper taken event failed — assignment rolled back")
            await callback.answer(
                "Не удалось закрепить задачу: ошибка отправки события",
                show_alert=True,
            )
            return
        self._register_pending_ack(
            op="taken",
            url=url,
            master_chat_id=message_obj.chat.id,
            master_thread_id=(
                message_obj.message_thread_id
                or (
                    settings.master_thread
                    if (settings := bot_config.get_bot_setting(bot.id)) is not None
                    else None
                )
            ),
            agent_username=agent_username,
            bot=bot,
        )

        await callback.answer(f"Задача закрепляется за {callback.from_user.username}")
        await self._install_take_close_button(
            bot=bot,
            ticket_message=message_obj,
            client_user_id=callback_data.user_id,
            agent_user_id=callback.from_user.id,
            agent_username=agent_username,
        )

    async def callbacks_lang_end(
        self, callback: types.CallbackQuery, callback_data: EndCallbackData
    ):
        message = callback.message
        bot = callback.bot
        if message is None:
            await callback.answer("Не удалось обработать сообщение", show_alert=True)
            return
        if bot is None:
            await callback.answer("Не удалось отправить событие", show_alert=True)
            return
        assert bot is not None
        message_obj = cast(Message, message)

        if callback_data.user_id == 0:
            await callback.answer(f"Задача закрыта {callback_data.username} !")
            return
        if callback_data.user_id != callback.from_user.id:
            await callback.answer(
                f"Задача закреплена за {callback_data.username} !", show_alert=True
            )
            return

        agent_username = callback.from_user.username or callback_data.username or "-"
        url = _extract_message_url(message_obj) or ""

        if not _is_valid_url(url):
            await callback.answer(
                "Не удалось отправить событие: некорректный URL", show_alert=True
            )
            return

        try:
            current = await _get_ticket_assignment(
                bot_id=bot.id,
                user_id=callback_data.ticket_user_id,
                ticket_msg_id=message_obj.message_id,
                raise_on_error=True,
            )
        except Exception:
            logger.exception("helper close ticket read failed")
            await callback.answer(
                "Не удалось закрыть тикет: Redis недоступен", show_alert=True
            )
            return
        if current is None:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=message_obj.chat.id,
                    message_id=message_obj.message_id,
                    reply_markup=_closed_reply_markup(
                        callback_data.ticket_user_id, agent_username
                    ),
                )
            except Exception as ex:
                logger.warning(
                    "helper stale close keyboard cleanup failed — bot_id={}, "
                    "ticket_msg_id={}: {}",
                    bot.id,
                    message_obj.message_id,
                    ex,
                )
            await callback.answer("Задача уже закрыта")
            return
        allowed_usernames = {
            username.lstrip("@").lower()
            for username in (callback.from_user.username, callback_data.username)
            if username
        }
        if current.agent.lstrip("@").lower() not in allowed_usernames:
            await callback.answer(
                f"Тикет закреплён за {_plain_agent_tag(current.agent)}",
                show_alert=True,
            )
            return

        deleted = await _delete_ticket_assignment(
            bot_id=bot.id,
            user_id=callback_data.ticket_user_id,
            ticket_msg_id=message_obj.message_id,
        )
        if deleted is None:
            await callback.answer("Не удалось удалить тикет из Redis", show_alert=True)
            return
        if deleted is False:
            await callback.answer("Тикет уже закрыт или переназначен", show_alert=True)
            return

        event_message = _build_closed_message(
            user_id=callback_data.ticket_user_id,
            agent_username=agent_username,
            url=url,
        )
        try:
            await bot.send_message(chat_id=HELPER_EVENTS_CHAT_ID, text=event_message)
        except Exception:
            logger.exception("helper closed event failed")
            await _restore_ticket_assignment(
                bot_id=bot.id,
                user_id=callback_data.ticket_user_id,
                ticket_msg_id=message_obj.message_id,
                agent_username=current.agent,
            )
            await callback.answer(
                "Не удалось закрыть тикет: ошибка отправки события",
                show_alert=True,
            )
            return
        self._register_pending_ack(
            op="closed",
            url=url,
            master_chat_id=message_obj.chat.id,
            master_thread_id=(
                message_obj.message_thread_id
                or (
                    settings.master_thread
                    if (settings := bot_config.get_bot_setting(bot.id)) is not None
                    else None
                )
            ),
            agent_username=agent_username,
            bot=bot,
        )

        reply_markup = _closed_reply_markup(
            callback_data.ticket_user_id, agent_username
        )
        await callback.answer(f"{agent_username} умничка !")
        try:
            await bot.edit_message_reply_markup(
                chat_id=message_obj.chat.id,
                message_id=message_obj.message_id,
                reply_markup=reply_markup,
            )
        except Exception as ex:
            logger.warning(
                "helper closed keyboard update failed — bot_id={}, "
                "ticket_msg_id={}: {}",
                bot.id,
                message_obj.message_id,
                ex,
            )

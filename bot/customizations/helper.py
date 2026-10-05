import asyncio
import json
import re
from dataclasses import dataclass
from html import escape
from typing import cast
from urllib.parse import quote, unquote

import redis.asyncio as aioredis
from aiogram import Bot, F, Router, types
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, Message
from loguru import logger

from .interface import AbstractBotCustomization
from .registry import register_customization
from config.bot_config import SupportBotSettings, bot_config


HELPER_EVENTS_CHAT_ID = -1002263825546
HELPER_BOT_ID = 5173438724
ACK_TIMEOUT_SECONDS = 300

_redis_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(bot_config.REDIS_URL, decode_responses=True)
    return _redis_client


def _assignment_key(bot_id: int, user_id: int) -> str:
    return f"helper:assign:{bot_id}:{user_id}"


async def _set_assignment(
    *, bot_id: int, user_id: int, agent_username: str, ticket_msg_id: int
) -> None:
    payload = json.dumps({"agent": agent_username, "ticket_msg": ticket_msg_id})
    try:
        await _get_redis().set(_assignment_key(bot_id, user_id), payload)
    except Exception as ex:
        logger.warning(
            f"helper assignment save failed — bot_id={bot_id}, user_id={user_id}: {ex}"
        )


@dataclass
class Assignment:
    agent: str
    ticket_msg: int


def _parse_assignment(raw: str | None) -> Assignment | None:
    """Parse a stored assignment; corrupted entries degrade to None."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return Assignment(
            agent=str(data.get("agent") or ""), ticket_msg=int(data["ticket_msg"])
        )
    except Exception as ex:
        logger.warning(f"helper assignment parse failed for {raw!r}: {ex}")
        return None


async def _get_assignment(*, bot_id: int, user_id: int) -> Assignment | None:
    try:
        raw = await _get_redis().get(_assignment_key(bot_id, user_id))
    except Exception as ex:
        logger.warning(
            f"helper assignment read failed — bot_id={bot_id}, user_id={user_id}: {ex}"
        )
        return None
    return _parse_assignment(raw)


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
    assignments: list[tuple[str, Assignment]] = []
    ordered_keys = sorted(keys)
    for start in range(0, len(ordered_keys), 200):
        batch = ordered_keys[start : start + 200]
        raws = await _get_redis().mget(batch)
        for key, raw in zip(batch, raws, strict=True):
            assignment = _parse_assignment(raw)
            if assignment is not None:
                assignments.append((key, assignment))
    return assignments


async def _find_assignment_by_ticket(
    bot_id: int, ticket_msg_id: int
) -> tuple[str, Assignment] | None:
    """Stream assignments in batches and return the first one anchored to
    the given ticket message, without materializing the whole table."""
    batch: list[str] = []

    async def flush() -> tuple[str, Assignment] | None:
        if not batch:
            return None
        raws = await _get_redis().mget(batch)
        for key, raw in zip(batch, raws, strict=True):
            assignment = _parse_assignment(raw)
            if assignment is not None and assignment.ticket_msg == ticket_msg_id:
                return key, assignment
        batch.clear()
        return None

    async for key in _get_redis().scan_iter(
        match=f"helper:assign:{bot_id}:*", count=100
    ):
        batch.append(key)
        if len(batch) >= 200:
            found = await flush()
            if found is not None:
                return found
    return await flush()


async def _claim_assignment(
    *,
    bot_id: int,
    user_id: int,
    agent_username: str,
    ticket_msg_id: int,
    previous: Assignment | None,
) -> bool:
    """Atomically claim the ticket for an agent.

    Fresh claims use SET NX — a concurrent click by another agent cannot
    win. Re-anchoring by the same agent is a plain overwrite. Redis errors
    propagate to the caller.
    """
    key = _assignment_key(bot_id, user_id)
    payload = json.dumps({"agent": agent_username, "ticket_msg": ticket_msg_id})
    result = await _get_redis().set(key, payload, nx=True if previous is None else None)
    return bool(result) if previous is None else True


async def _delete_assignment(*, bot_id: int, user_id: int) -> None:
    try:
        await _get_redis().delete(_assignment_key(bot_id, user_id))
    except Exception as ex:
        logger.warning(
            f"helper assignment delete failed — bot_id={bot_id}, user_id={user_id}: {ex}"
        )


def _format_agent_tag(agent: str) -> str:
    """Render an agent reference for HTML-parse-mode texts."""
    clean = escape(agent.lstrip("@"))
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


def _is_valid_url(url: str) -> bool:
    return bool(url) and (url.startswith("https://") or url.startswith("http://"))


def _extract_message_url(message: object) -> str:
    getter = getattr(message, "get_url", None)
    if not callable(getter):
        return ""
    url = getter()
    return url if isinstance(url, str) else ""


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
        f"agent_username={_encode_value(agent_username)} "
        f"url={_encode_value(url)}"
    )


def _build_closed_message(user_id: int, agent_username: str, url: str) -> str:
    return (
        "#skynet #helper "
        f"command=closed "
        f"user_id={user_id} "
        f"agent_username={_encode_value(agent_username)} "
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


@register_customization(bot_id=HELPER_BOT_ID)
class HelperCustomization(AbstractBotCustomization):
    def __init__(self):
        self._router: Router | None = None
        self._pending_acks: dict[tuple[str, str], PendingAck] = {}
        self._pending_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}

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
        self, message: Message, bot_settings: SupportBotSettings
    ) -> bool:
        """Core hook: /tickets and /ticket_close_{id} in the master chat."""
        text = message.text or ""
        is_tickets = bool(re.match(r"^/tickets(?:@\S+)?\s*$", text, re.IGNORECASE))
        is_close = bool(
            re.match(r"^/ticket_close_\d+(?:@\S+)?\s*$", text, re.IGNORECASE)
        )
        if not is_tickets and not is_close:
            return False
        if _foreign_command_mention(text, bot_settings.username):
            return False
        if is_tickets:
            await self.cmd_tickets(message, bot_settings)
        else:
            await self.cmd_ticket_close(message, bot_settings)
        return True

    async def cmd_tickets(
        self, message: Message, bot_settings: SupportBotSettings
    ) -> None:
        try:
            stored = await _scan_assignments(bot_settings.id)
        except Exception as ex:
            logger.warning(f"helper /tickets failed: {ex}")
            await message.answer(
                "Не удалось получить список тикетов, редис недоступен",
                disable_web_page_preview=True,
            )
            return
        # t.me/c/ links exist only for supergroup/channel ids (-100...);
        # anything else degrades to a plain message id without a link.
        link_ok = str(message.chat.id).startswith("-100")
        internal_chat_id = str(message.chat.id).removeprefix("-100")
        rows: list[tuple[int, str]] = []
        for key, assignment in stored:
            raw_user_id = key.rsplit(":", 1)[1]
            try:
                client_id = int(raw_user_id)
            except ValueError:
                logger.warning(f"helper assignment key skipped: {key!r}")
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
                    f" | закрыть /ticket_close_{ticket}",
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
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > 4000 and current:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)
        if not chunks:
            await message.answer("Открытых тикетов нет", disable_web_page_preview=True)
            return
        for index, chunk in enumerate(chunks):
            if index:
                await asyncio.sleep(0.5)  # stay clear of Telegram flood limits
            header = "Открытые тикеты:\n" if index == 0 else ""
            await message.answer(
                f"{header}{chunk}",
                disable_web_page_preview=True,
                parse_mode="HTML",
            )

    async def cmd_ticket_close(
        self, message: Message, bot_settings: SupportBotSettings
    ) -> None:
        match = re.match(
            r"^/ticket_close_(\d+)(?:@\S+)?\s*$",
            message.text or "",
            re.IGNORECASE,
        )
        if match is None:
            return
        ticket_msg_id = int(match.group(1))
        try:
            found = await _find_assignment_by_ticket(bot_settings.id, ticket_msg_id)
        except Exception as ex:
            logger.warning(f"helper /ticket_close failed: {ex}")
            await message.answer(
                "Не удалось закрыть тикет, редис недоступен",
                disable_web_page_preview=True,
            )
            return
        if found is None:
            await message.answer(
                f"Открытый тикет с сообщением {ticket_msg_id} не найден",
                disable_web_page_preview=True,
            )
            return
        key, assignment = found
        try:
            client_id = int(key.rsplit(":", 1)[1])
        except ValueError:
            client_id = None
        # Re-check the anchor right before deleting: the assignment may have
        # been re-taken to a newer ticket since the scan above.
        current = (
            await _get_assignment(bot_id=bot_settings.id, user_id=client_id)
            if client_id is not None
            else None
        )
        if current is None or current.ticket_msg != ticket_msg_id:
            await message.answer(
                "Этот тикет уже закрыт или закреплён за другим сообщением",
                disable_web_page_preview=True,
            )
            return
        try:
            await _get_redis().delete(key)
        except Exception as ex:
            logger.warning(f"helper /ticket_close delete failed: {ex}")
            await message.answer(
                "Не удалось закрыть тикет, редис недоступен",
                disable_web_page_preview=True,
            )
            return
        agent = _format_agent_tag(assignment.agent)
        await message.answer(
            f"Тикет {ticket_msg_id} закрыт (взял {agent})",
            disable_web_page_preview=True,
            parse_mode="HTML",
        )

    async def get_extra_text(
        self, user: types.User, message: Message, bot_settings: SupportBotSettings
    ) -> str:
        extra = f"\n/get_info_{user.id}@mymtlbot"
        assignment = await _get_assignment(bot_id=bot_settings.id, user_id=user.id)
        if assignment is not None and assignment.agent:
            extra += f"\nCC {_format_agent_tag(assignment.agent)}"
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
        assignment = await _get_assignment(bot_id=bot_settings.id, user_id=user.id)
        if assignment is not None:
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

        existing = await _get_assignment(bot_id=bot.id, user_id=callback_data.user_id)
        if (
            existing is not None
            and existing.agent.lstrip("@").lower() != agent_username.lstrip("@").lower()
        ):
            # the ticket is already owned by another agent; stealing it would
            # silently discard their assignment
            await callback.answer(
                f"Тикет уже взят {_format_agent_tag(existing.agent)}!",
                show_alert=True,
            )
            return
        previous = existing

        # Claim the assignment atomically BEFORE the channel round-trip: two
        # agents clicking within the same moment must not both pass the check
        # above (the Telegram send yields to the event loop for hundreds of
        # ms, so a check-then-act gap would be a real race window).
        try:
            claimed = await _claim_assignment(
                bot_id=bot.id,
                user_id=callback_data.user_id,
                agent_username=agent_username,
                ticket_msg_id=message_obj.message_id,
                previous=previous,
            )
        except Exception as ex:
            logger.warning(f"helper take claim failed: {ex}")
            await callback.answer(
                "Не удалось закрепить задачу, Redis недоступен", show_alert=True
            )
            return
        if not claimed:
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
            if previous is None:
                await _delete_assignment(bot_id=bot.id, user_id=callback_data.user_id)
            else:
                await _set_assignment(
                    bot_id=bot.id,
                    user_id=callback_data.user_id,
                    agent_username=previous.agent,
                    ticket_msg_id=previous.ticket_msg,
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
            master_thread_id=message_obj.message_thread_id,
            agent_username=agent_username,
            bot=bot,
        )

        await callback.answer(f"Задача закрепляется за {callback.from_user.username}")
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=f"Взял {callback.from_user.username}",
                        callback_data=EndCallbackData(
                            ticket_user_id=callback_data.user_id,
                            user_id=callback.from_user.id,
                            username=str(callback.from_user.username or "-"),
                        ).pack(),
                    )
                ]
            ]
        )
        await bot.edit_message_reply_markup(
            chat_id=message_obj.chat.id,
            message_id=message_obj.message_id,
            reply_markup=reply_markup,
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

        event_message = _build_closed_message(
            user_id=callback_data.ticket_user_id,
            agent_username=agent_username,
            url=url,
        )
        await bot.send_message(chat_id=HELPER_EVENTS_CHAT_ID, text=event_message)
        self._register_pending_ack(
            op="closed",
            url=url,
            master_chat_id=message_obj.chat.id,
            master_thread_id=message_obj.message_thread_id,
            agent_username=agent_username,
            bot=bot,
        )
        # Release the assignment only when it is still anchored to this
        # ticket message: the user may already have a newer ticket taken
        # by someone else, and closing this stale button must not wipe it.
        current = await _get_assignment(
            bot_id=bot.id, user_id=callback_data.ticket_user_id
        )
        if current is not None and current.ticket_msg == message_obj.message_id:
            await _delete_assignment(
                bot_id=bot.id, user_id=callback_data.ticket_user_id
            )

        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=f"Закрыл {callback.from_user.username}",
                        callback_data=EndCallbackData(
                            ticket_user_id=callback_data.ticket_user_id,
                            user_id=0,
                            username=callback_data.username,
                        ).pack(),
                    )
                ]
            ]
        )
        await bot.edit_message_reply_markup(
            chat_id=message_obj.chat.id,
            message_id=message_obj.message_id,
            reply_markup=reply_markup,
        )
        await callback.answer(f"{callback_data.username} умничка !")

import asyncio
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote, unquote

from aiogram import Bot, F, Router, types
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, Message
from loguru import logger

from .interface import AbstractBotCustomization
from .registry import register_customization
from config.bot_config import SupportBotSettings


HELPER_EVENTS_CHAT_ID = -1002263825546
ACK_TIMEOUT_SECONDS = 300


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


@register_customization(bot_id=5173438724)
class HelperCustomization(AbstractBotCustomization):
    def __init__(self):
        self._router = Router()
        self._pending_acks: dict[tuple[str, str], PendingAck] = {}
        self._pending_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._register_handlers()

    @property
    def router(self) -> Router:
        return self._router

    def _register_handlers(self):
        self._router.callback_query.register(
            self.callbacks_lang_get, GetCallbackData.filter()
        )
        self._router.callback_query.register(
            self.callbacks_lang_end, EndCallbackData.filter()
        )
        self._router.channel_post.register(
            self.handle_helper_channel_post, F.text.regexp(r"^\s*#helper\b")
        )
        self._router.channel_post.register(
            self.handle_helper_channel_post, F.caption.regexp(r"^\s*#helper\b")
        )

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

    async def get_extra_text(
        self, user: types.User, message: Message, bot_settings: SupportBotSettings
    ) -> str:
        return f"\n/get_info_{user.id}@mymtlbot"

    async def get_reply_markup(
        self, user: types.User, message: Message, bot_settings: SupportBotSettings
    ) -> InlineKeyboardMarkup:
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

        agent_username = callback.from_user.username or "-"
        url = _extract_message_url(message_obj) or ""

        if not _is_valid_url(url):
            await callback.answer(
                "Не удалось отправить событие: некорректный URL", show_alert=True
            )
            return

        event_message = _build_taken_message(
            user_id=callback_data.user_id,
            username=callback_data.username,
            agent_username=agent_username,
            url=url,
        )
        await bot.send_message(chat_id=HELPER_EVENTS_CHAT_ID, text=event_message)
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

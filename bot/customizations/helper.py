from aiogram import Router, types
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, Message
from typing import cast
from urllib.parse import quote

from .interface import AbstractBotCustomization
from .registry import register_customization
from config.bot_config import SupportBotSettings


HELPER_EVENTS_CHAT_ID = -1002263825546


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

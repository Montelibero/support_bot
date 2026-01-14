from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, Message
from .interface import AbstractBotCustomization
from .registry import register_customization
from config.bot_config import SupportBotSettings

@register_customization(bot_id=123)
class TestCustomization(AbstractBotCustomization):
    def __init__(self):
        self._router = Router()

    @property
    def router(self) -> Router:
        return self._router

    async def get_extra_text(self, user: types.User, message: Message, bot_settings: SupportBotSettings) -> str:
        return "\n[TEST MODE ACTIVATED]"

    async def get_reply_markup(self, user: types.User, message: Message, bot_settings: SupportBotSettings) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="Test Button", url="https://google.com")
        ]])

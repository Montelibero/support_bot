from aiogram import Router, types
from aiogram.types import Message
from .interface import AbstractBotCustomization
from config.bot_config import SupportBotSettings


class DefaultBotCustomization(AbstractBotCustomization):
    def __init__(self):
        self._router = Router()

    @property
    def router(self) -> Router:
        return self._router

    async def get_extra_text(
        self, user: types.User, message: Message, bot_settings: SupportBotSettings
    ) -> str:
        return ""

    async def get_reply_markup(
        self, user: types.User, message: Message, bot_settings: SupportBotSettings
    ) -> None:
        return None

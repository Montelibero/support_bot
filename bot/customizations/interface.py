from abc import ABC, abstractmethod
from typing import Optional
from aiogram.types import InlineKeyboardMarkup, Message, User
from aiogram import Router
from config.bot_config import SupportBotSettings


class AbstractBotCustomization(ABC):
    @property
    @abstractmethod
    def router(self) -> Router:
        """Returns the router with customization-specific handlers."""
        pass

    @abstractmethod
    async def get_extra_text(
        self, user: User, message: Message, bot_settings: SupportBotSettings
    ) -> str:
        """Returns extra text to append to the forwarded message."""
        pass

    @abstractmethod
    async def get_reply_markup(
        self, user: User, message: Message, bot_settings: SupportBotSettings
    ) -> Optional[InlineKeyboardMarkup]:
        """Returns the reply markup (buttons) for the forwarded message."""
        pass

    async def handle_master_message(
        self, message: Message, bot_settings: SupportBotSettings
    ) -> bool:
        """Called by the core for every master-chat message before default
        handling. Return True when the message is fully handled; the core
        treats any exception here as "not handled" and keeps working."""
        return False

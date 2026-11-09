from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional
from aiogram import Bot, Router
from aiogram.types import InlineKeyboardMarkup, Message, User
from config.bot_config import SupportBotSettings

if TYPE_CHECKING:
    from database.repositories import Repo


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
        self,
        message: Message,
        bot_settings: SupportBotSettings,
        *,
        bot: Bot | None = None,
        repo: Repo | None = None,
    ) -> bool:
        """Called by the core for every master-chat message before default
        handling. Return True when the message is fully handled; the core
        treats any exception here as "not handled" and keeps working."""
        return False

    async def get_help_text(self, bot_settings: SupportBotSettings) -> str:
        """Return a valid HTML fragment appended to /help.

        The core owns the header and the base command list; plugins only add
        their own section. Return an empty string when the plugin has no
        commands to document.
        """
        return ""

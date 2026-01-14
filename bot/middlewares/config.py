from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram import Bot

from config.bot_config import BotConfig


class ConfigMiddleware(BaseMiddleware):
    def __init__(self, config: BotConfig):
        self.config = config

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["config"] = self.config
        
        bot: Bot = data.get("bot")
        if bot:
            bot_settings = self.config.get_bot_setting(bot.id)
            if bot_settings:
                data["bot_settings"] = bot_settings
                
        return await handler(event, data)

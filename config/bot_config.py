import json
import os
from dataclasses import dataclass, field
from typing import Dict, Optional, List

import asyncio

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
from environs import Env
from pydantic import BaseModel
from loguru import logger

env = Env()
try:
    env.read_env()
except Exception as e:
    logger.error(f"Error reading .env file: {e}")


class SupportBotSettings(BaseModel):
    """Model for support bot settings."""
    id: int
    username: str
    token: str
    start_message: str
    security_policy: str
    master_chat: Optional[int] = None
    master_thread: Optional[int] = None
    no_start_message: bool
    special_commands: int
    mark_bad: bool
    owner: int
    can_work: bool = False
    ignore_commands: bool = False
    use_local_names: bool = False # todo
    local_names: dict = {}
    use_auto_reply: bool = False
    block_links: bool = True
    auto_reply: str = "Message automatically forwarded to support. Please wait for a response."
    ignore_users: list = []


@dataclass
class BotConfig:
    """Singleton class for bot configuration and database operations."""
    bot_setting: Dict[str, Dict] = field(default_factory=dict)
    media_groups: Dict[str, List] = field(default_factory=dict)
    main_bot_token: str = field(default_factory=lambda: env.str("BOT_TOKEN"))

    single_bot_token: Optional[str] = field(default_factory=lambda: env.str("SINGLE_BOT_TOKEN", None))
    BASE_URL: str = field(default_factory=lambda: env.str("BASE_URL"))
    SECRET_URL: str = field(default_factory=lambda: env.str("SECRET_URL"))
    REDIS_URL: str = field(default_factory=lambda: env.str("REDIS_URL"))
    SENTRY_DSN: str = field(default_factory=lambda: env.str("SENTRY_DSN"))
    ADMIN_ID: int = field(default_factory=lambda: env.int("ADMIN_ID", 84131737))
    
    # Parameters for single bot
    SINGLE_START_MESSAGE: str = field(default_factory=lambda: env.str("SINGLE_START_MESSAGE", "Здравствуйте! Чем могу помочь?"))
    SINGLE_SECURITY_POLICY: str = field(default_factory=lambda: env.str("SINGLE_SECURITY_POLICY", "default"))
    SINGLE_MASTER_CHAT: int = field(default_factory=lambda: env.int("SINGLE_MASTER_CHAT", 0))  # 0 means use ADMIN_ID
    SINGLE_USE_AUTO_REPLY: bool = field(default_factory=lambda: env.bool("SINGLE_USE_AUTO_REPLY", False))
    SINGLE_AUTO_REPLY: str = field(default_factory=lambda: env.str("SINGLE_AUTO_REPLY", "Message automatically forwarded to support. Please wait for a response."))

    WEB_SERVER_HOST: str = "0.0.0.0"
    WEB_SERVER_PORT: int = 80
    MAIN_BOT_PATH: str = "main"
    OTHER_BOTS_PATH: str = "bot/{bot_token}"


    SQLITE_FILE_NAME: str = field(
        default_factory=lambda: os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'support.db'))

    json_config: dict = field(default_factory=dict)

    def __post_init__(self):
        # Initial empty config; will be populated via load_from_db()
        self.json_config = {}

    @property
    def other_bots_url(self) -> str:
        return f"{self.BASE_URL}/{bot_config.SECRET_URL}/{self.OTHER_BOTS_PATH}"

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load_from_db(self) -> None:
        """Load bot settings from the database into memory synchronously (for startup)."""
        import sqlite3

        
        try:
            self.json_config = {}
            if not os.path.exists(self.SQLITE_FILE_NAME):
                 logger.warning(f"Database file not found: {self.SQLITE_FILE_NAME}. Skipping load.")
                 return

            conn = sqlite3.connect(self.SQLITE_FILE_NAME)
            # Use dictionary cursor
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            try:
                cursor.execute("SELECT * FROM bot_settings")
                rows = cursor.fetchall()
                
                for row in rows:
                    bot_id = row['id']
                    
                    # Manual mapping and JSON parsing
                    bot_dict = {
                        "id": row['id'],
                        "username": row['username'],
                        "token": row['token'],
                        "start_message": row['start_message'],
                        "security_policy": row['security_policy'],
                        "master_chat": row['master_chat'],
                        "master_thread": row['master_thread'],
                        "no_start_message": bool(row['no_start_message']),
                        "special_commands": row['special_commands'],
                        "mark_bad": bool(row['mark_bad']),
                        "owner": row['owner'],
                        "can_work": bool(row['can_work']),
                        "ignore_commands": bool(row['ignore_commands']),
                        "use_local_names": bool(row['use_local_names']),
                        # JSON fields might be stored as strings or JSON types depending on SQLite version/driver
                        # In standard sqlite3, they are strings if stored as JSON
                        "local_names": json.loads(row['local_names']) if isinstance(row['local_names'], str) else row['local_names'],
                        "use_auto_reply": bool(row['use_auto_reply']),
                        "block_links": bool(row['block_links']),
                        "auto_reply": row['auto_reply'],
                        "ignore_users": json.loads(row['ignore_users']) if isinstance(row['ignore_users'], str) else row['ignore_users'],
                    }
                    
                    # Handle potential edge cases where JSON fields are empty or none
                    if bot_dict["local_names"] is None: bot_dict["local_names"] = {}
                    if bot_dict["ignore_users"] is None: bot_dict["ignore_users"] = []

                    self.json_config[str(bot_id)] = bot_dict
                    
                logger.info(f"Loaded {len(self.json_config)} bot settings from DB")
            except Exception as e:
                # Table might not exist yet if migration hasn't run
                logger.warning(f"Could not load settings from DB (table might be missing): {e}")
            finally:
                conn.close()
                
        except Exception as e:
            logger.error(f"Error loading settings from DB: {e}")

    def get_bot_settings(self) -> List[SupportBotSettings]:
        """Retrieve all bot settings."""
        try:
            return [SupportBotSettings(**bot) for bot in self.json_config.values()]
        except Exception as e:
            logger.error(f"Error retrieving bot settings: {e}")
            return []

    def get_bot_setting(self, bot_id: int) -> Optional[SupportBotSettings]:
        """Retrieve bot settings by ID."""
        try:
            result = self.json_config.get(str(bot_id))
            return SupportBotSettings(**result) if result else None
        except Exception as e:
            logger.error(f"Error retrieving bot settings: {e}")
            return None

    async def save_settings_to_db(self, settings: SupportBotSettings) -> None:
        """Save a single bot setting to DB and update cache."""
        from database.models import BotSettings, session_maker
        from sqlalchemy import select
        
        try:
            async with session_maker() as session:
                # Check if exists
                stmt = select(BotSettings).filter(BotSettings.id == settings.id)
                result = await session.execute(stmt)
                bot_db = result.scalars().first()
                
                if bot_db:
                    # Update existing
                    bot_db.username = settings.username
                    bot_db.token = settings.token
                    bot_db.start_message = settings.start_message
                    bot_db.security_policy = settings.security_policy
                    bot_db.master_chat = settings.master_chat
                    bot_db.master_thread = settings.master_thread
                    bot_db.no_start_message = settings.no_start_message
                    bot_db.special_commands = settings.special_commands
                    bot_db.mark_bad = settings.mark_bad
                    bot_db.owner = settings.owner
                    bot_db.can_work = settings.can_work
                    bot_db.ignore_commands = settings.ignore_commands
                    bot_db.use_local_names = settings.use_local_names
                    bot_db.local_names = settings.local_names
                    bot_db.use_auto_reply = settings.use_auto_reply
                    bot_db.block_links = settings.block_links
                    bot_db.auto_reply = settings.auto_reply
                    bot_db.ignore_users = settings.ignore_users
                else:
                    # Create new
                    bot_db = BotSettings(
                        id=settings.id,
                        username=settings.username,
                        token=settings.token,
                        start_message=settings.start_message,
                        security_policy=settings.security_policy,
                        master_chat=settings.master_chat,
                        master_thread=settings.master_thread,
                        no_start_message=settings.no_start_message,
                        special_commands=settings.special_commands,
                        mark_bad=settings.mark_bad,
                        owner=settings.owner,
                        can_work=settings.can_work,
                        ignore_commands=settings.ignore_commands,
                        use_local_names=settings.use_local_names,
                        local_names=settings.local_names,
                        use_auto_reply=settings.use_auto_reply,
                        block_links=settings.block_links,
                        auto_reply=settings.auto_reply,
                        ignore_users=settings.ignore_users
                    )
                    session.add(bot_db)
                
                await session.commit()
                
            # Update cache
            self.json_config[str(settings.id)] = settings.model_dump()
            
        except Exception as e:
            logger.error(f"Error saving setting to DB: {e}")

    async def delete_bot_setting(self, bot_id: int) -> None:
        """Delete bot settings by ID."""
        from database.models import BotSettings, session_maker
        from sqlalchemy import select

        try:
            bot_id_str = str(bot_id)
            
            # Delete from DB
            async with session_maker() as session:
                 stmt = select(BotSettings).filter(BotSettings.id == bot_id)
                 result = await session.execute(stmt)
                 bot_db = result.scalars().first()
                 if bot_db:
                     await session.delete(bot_db)
                     await session.commit()
                 else:
                     logger.warning(f"Attempt to delete non-existent bot in DB with ID: {bot_id}")

            # Delete from internal cache
            if bot_id_str in self.json_config:
                del self.json_config[bot_id_str]
            else:
                logger.warning(f"Attempt to delete non-existent bot in cache with ID: {bot_id}")
                
        except Exception as e:
            logger.error(f"Error deleting bot settings: {e}")

    async def update_bot_setting(self, settings: SupportBotSettings) -> None:
        """Update existing bot settings."""
        await self.save_settings_to_db(settings)


async def set_commands(bot):
    commands_private = [
        BotCommand(
            command="start",
            description="Start or ReStart bot",
        ),
    ]
    await bot.set_my_commands(commands=commands_private, scope=BotCommandScopeAllPrivateChats())


async def set_webhook(bot: Bot, url: str):
    await set_commands(bot)
    await bot.set_webhook(url=url)


async def delete_webhook(bot: Bot):
    await bot.delete_webhook()


# Создание экземпляра BotConfig
bot_config = BotConfig()

if __name__ == '__main__':
    print(bot_config.get_bot_setting(5637021560))
    # for r in bot_config.get_bot_settings():
    #    print(r)

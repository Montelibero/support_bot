# Contributing Guide

## Bot Customizations

This project supports a plugin-like system to add custom functionality for specific bots without modifying the core logic. This is useful for adding unique buttons, text injections, or custom handlers for a specific bot instance.

### How it Works

The system uses a **Registry** pattern. Each bot is identified by its `bot_id` (integer). When the Support Bot forwards a message, it checks if there is a registered customization for that `bot_id`.

- **Registry**: `bot/customizations/registry.py`
- **Interface**: `bot/customizations/interface.py`
- **Loader**: `bot/customizations/loader.py`

### Adding a New Customization

To add custom behavior for a bot (e.g., Bot ID `123456789`), follow these steps:

1.  **Create a file**: Create a new python file in `bot/customizations/`, e.g., `bot/customizations/my_custom_bot.py`.

2.  **Implement the Interface**:
    Inherit from `AbstractBotCustomization` and implement the required methods.

    ```python
    from aiogram import Router, types
    from aiogram.types import InlineKeyboardMarkup, Message
    from .interface import AbstractBotCustomization
    from .registry import register_customization
    from config.bot_config import SupportBotSettings

    # Register your class with the specific Bot ID
    @register_customization(bot_id=123456789)
    class MyCustomBot(AbstractBotCustomization):
        def __init__(self):
            self._router = Router()
            # Register local handlers if needed
            # self._router.message.register(self.my_handler)

        @property
        def router(self) -> Router:
            return self._router

        async def get_extra_text(self, user: types.User, message: Message, bot_settings: SupportBotSettings) -> str:
            # Return text to be appended to the forwarded message
            return "\n[Custom Bot Tag]"

        async def get_reply_markup(self, user: types.User, message: Message, bot_settings: SupportBotSettings) -> InlineKeyboardMarkup:
            # Return buttons to be attached to the forwarded message
            return InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="Custom Action", callback_data="my_action")
            ]])
    ```

3.  **Register the Module**:
    Import your new module in `bot/customizations/__init__.py` or `bot/customizations/loader.py` to ensure the `@register_customization` decorator runs on startup.

    *File: `bot/customizations/loader.py`*
    ```python
    # ... existing imports
    from . import helper
    from . import test_customization
    from . import my_custom_bot  # <--- Add this line
    ```

### Testing

You can use the `test_customization.py` as a reference for writing unit tests for your new customization.

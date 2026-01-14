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

### Webhook Allowed Updates

When running in **Webhook mode** (Production), the list of allowed update types (e.g., `message`, `callback_query`, `message_reaction`) is calculated automatically based **only on the Main Bot's handlers**.

If your customization introduces a handler for an update type that the Main Bot does NOT use (e.g., `message_reaction`), you **must** manually ensure it is included in the `allowed_updates` list in `main.py`.

In `main.py`, the `aiogram_on_startup_webhook` function is responsible for this:

```python
async def aiogram_on_startup_webhook(dispatcher: Dispatcher, bot: Bot) -> None:
    allowed_updates = dispatcher.resolve_used_update_types()
    
    # If your customization uses 'message_reaction', verify it's here:
    if 'message_reaction' not in allowed_updates:
        allowed_updates.append('message_reaction')
        
    # ...
```

Failure to do this will result in Telegram **never sending** those specific updates to your bot, even if the handlers are correctly registered.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.exceptions import TelegramUnauthorizedError
from config.bot_config import SupportBotSettings
from main import aiogram_on_startup_webhook


@pytest.mark.asyncio
async def test_startup_unauthorized_error():
    # Mock dispatcher
    mock_dispatcher = MagicMock()
    mock_dispatcher.resolve_used_update_types.return_value = ["message"]

    # Mock main bot
    mock_main_bot = AsyncMock()

    # Mock bot_config
    with patch("main.bot_config") as mock_config:
        # Create a bot setting object that is enabled
        bot_setting = SupportBotSettings(
            id=123,
            username="test_bot",
            token="invalid_token",
            start_message="Hi",
            security_policy="default",
            no_start_message=False,
            special_commands=0,
            mark_bad=False,
            owner=1,
            can_work=True,
        )
        # return list with one bot
        mock_config.get_bot_settings.return_value = [bot_setting]
        mock_config.save_settings_to_db = AsyncMock()
        mock_config.BASE_URL = "https://example.com"
        mock_config.SECRET_URL = "secret"
        mock_config.MAIN_BOT_PATH = "main"
        # Mock property
        type(mock_config).other_bots_url = "https://example.com/secret/bot/{bot_token}"

        # Mock Bot constructor to raise TelegramUnauthorizedError
        # Inside the function, Bot(token=...) is called.
        with patch(
            "main.Bot",
            side_effect=TelegramUnauthorizedError(
                method=MagicMock(), message="Unauthorized"
            ),
        ):
            # Mock set_commands to avoid other errors/dependencies
            with patch("config.bot_config.set_commands", new=AsyncMock()):
                # Execute the function
                await aiogram_on_startup_webhook(mock_dispatcher, mock_main_bot)

        # Assertions
        # 1. Check if can_work was set to False
        assert bot_setting.can_work is False, "Bot should be disabled (can_work=False)"

        # 2. Check if save_settings_to_db was called
        mock_config.save_settings_to_db.assert_called_once_with(bot_setting)

        # 3. Check failure didn't propagate (function finished)
        assert True


@pytest.mark.asyncio
async def test_startup_skips_disabled_bot_without_telegram_calls():
    mock_dispatcher = MagicMock()
    mock_dispatcher.resolve_used_update_types.return_value = ["message"]
    mock_main_bot = AsyncMock()

    with patch("main.bot_config") as mock_config:
        disabled_bot = SupportBotSettings(
            id=7817659826,
            username="CaiusCosadesBot",
            token="invalid_token",
            start_message="Hi",
            security_policy="default",
            no_start_message=False,
            special_commands=0,
            mark_bad=False,
            owner=1,
            can_work=False,
        )
        mock_config.get_bot_settings.return_value = [disabled_bot]
        mock_config.save_settings_to_db = AsyncMock()
        mock_config.BASE_URL = "https://example.com"
        mock_config.SECRET_URL = "secret"
        mock_config.MAIN_BOT_PATH = "main"
        type(mock_config).other_bots_url = "https://example.com/secret/bot/{bot_token}"

        with patch("main.Bot") as mock_bot_ctor:
            with patch("config.bot_config.set_commands", new=AsyncMock()):
                await aiogram_on_startup_webhook(mock_dispatcher, mock_main_bot)

        mock_bot_ctor.assert_not_called()
        mock_config.save_settings_to_db.assert_not_called()


@pytest.mark.asyncio
async def test_startup_closes_tmp_bot_session_after_webhook_setup():
    mock_dispatcher = MagicMock()
    mock_dispatcher.resolve_used_update_types.return_value = ["message"]
    mock_main_bot = AsyncMock()

    with patch("main.bot_config") as mock_config:
        enabled_bot = SupportBotSettings(
            id=999,
            username="enabled_bot",
            token="valid_token",
            start_message="Hi",
            security_policy="default",
            no_start_message=False,
            special_commands=0,
            mark_bad=False,
            owner=1,
            can_work=True,
        )
        mock_config.get_bot_settings.return_value = [enabled_bot]
        mock_config.save_settings_to_db = AsyncMock()
        mock_config.BASE_URL = "https://example.com"
        mock_config.SECRET_URL = "secret"
        mock_config.MAIN_BOT_PATH = "main"
        type(mock_config).other_bots_url = "https://example.com/secret/bot/{bot_token}"

        tmp_bot = AsyncMock()
        bot_ctx_manager = AsyncMock()
        bot_ctx_manager.__aenter__.return_value = tmp_bot

        with patch("main.Bot", return_value=bot_ctx_manager):
            with patch("config.bot_config.set_commands", new=AsyncMock()):
                await aiogram_on_startup_webhook(mock_dispatcher, mock_main_bot)

        bot_ctx_manager.__aenter__.assert_awaited_once()
        bot_ctx_manager.__aexit__.assert_awaited_once()
        tmp_bot.set_webhook.assert_awaited_once()

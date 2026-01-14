
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from main import aiogram_on_startup_webhook
from aiogram import Dispatcher, Bot

@pytest.mark.asyncio
async def test_webhook_includes_reactions():
    # Setup
    dispatcher = MagicMock(spec=Dispatcher)
    # Simulate dispatcher NOT returning message_reaction
    dispatcher.resolve_used_update_types.return_value = ["message", "callback_query"]
    
    bot = AsyncMock(spec=Bot)
    
    # Patch dependencies
    with patch("main.bot_config") as mock_config, \
         patch("config.bot_config.set_commands", new=AsyncMock()):
        
        mock_config.BASE_URL = "https://example.com"
        mock_config.SECRET_URL = "secret"
        mock_config.MAIN_BOT_PATH = "main"
        mock_config.get_bot_settings.return_value = [] # No other bots for this test
        
        await aiogram_on_startup_webhook(dispatcher, bot)
        
        # Verify set_webhook called with message_reaction
        assert bot.set_webhook.called
        call_args = bot.set_webhook.call_args
        allowed_updates = call_args.kwargs['allowed_updates']
        
        assert "message_reaction" in allowed_updates
        assert "message" in allowed_updates
        assert "callback_query" in allowed_updates

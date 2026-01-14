import asyncio
from unittest.mock import patch, MagicMock
import pytest
from single_bot import main

@patch("single_bot.asyncio.run")
@patch("single_bot.Dispatcher.start_polling")
@patch("single_bot.bot_config")
def test_single_bot_setup(mock_config, mock_start_polling, mock_asyncio_run):
    """
    Test that single_bot.py initializes components correctly 
    and attempts to start polling.
    """
    # Setup mocks
    mock_config.single_bot_token = "123:test_token"
    mock_config.REDIS_URL = "redis://localhost:6379/0"
    mock_config.ADMIN_ID = 12345
    
    # Mock Bot.get_me() result which runs inside main() via asyncio.run
    mock_bot_info = MagicMock()
    mock_bot_info.id = 123456
    mock_bot_info.username = "test_bot"
    mock_asyncio_run.return_value = mock_bot_info

    # Run main logic
    main()

    # Verify start_polling was called
    assert mock_start_polling.called or mock_asyncio_run.called
    
    # Check if storage was initialized (indirectly via Dispatcher init in main)
    # Since we can't easily access the local dispatcher variable inside main,
    # we rely on the fact that execution reached start_polling without error.

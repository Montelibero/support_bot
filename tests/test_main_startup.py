import os
from unittest.mock import patch, MagicMock
from main import main

@patch("main.asyncio.run")
@patch("main.Dispatcher.start_polling", new_callable=MagicMock)
@patch("main.bot_config")
@patch("main.RedisStorage")
def test_main_polling_setup(mock_redis_storage, mock_config, mock_start_polling, mock_asyncio_run):
    """
    Test that main.py initializes in POLLING mode (default) correctly.
    """
    from aiogram.fsm.storage.memory import MemoryStorage
    mock_redis_storage.from_url.return_value = MemoryStorage()
    
    # Setup mocks
    mock_config.main_bot_token = "123:main_token"
    mock_config.REDIS_URL = "redis://localhost:6379/0"
    
    # Run main logic
    main()
    
    # Verify execution flow
    assert mock_start_polling.called or mock_asyncio_run.called

@patch("aiohttp.web.run_app")
@patch("main.bot_config")
@patch("main.RedisStorage")
def test_main_webhook_setup(mock_redis_storage, mock_config, mock_run_app):
    """
    Test that main.py initializes in WEBHOOK mode (ENVIRONMENT=production).
    """
    from aiogram.fsm.storage.memory import MemoryStorage
    mock_redis_storage.from_url.return_value = MemoryStorage()
    
    # Setup mocks
    mock_config.main_bot_token = "123:main_token"
    mock_config.REDIS_URL = "redis://localhost:6379/0"
    mock_config.get_bot_settings.return_value = [] # No extra bots
    mock_config.OTHER_BOTS_PATH = "bot/{bot_token}"
    mock_config.SECRET_URL = "secret_path"
    mock_config.MAIN_BOT_PATH = "main"
    
    # Set Environment
    with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        main()
        
    # Verify web app run
    assert mock_run_app.called

import os
import inspect
import pytest
from unittest.mock import patch, MagicMock
from main import LocalApiTokenBasedRequestHandler, main


def test_support_webhook_waits_for_durable_enqueue():
    source = inspect.getsource(main)

    assert "handle_in_background=False" in source
    assert "delivery_queue=delivery_queue" in source


@pytest.mark.asyncio
async def test_runtime_bot_uses_factory_and_registers_for_delivery():
    registry = {}
    dispatcher = MagicMock()
    handler = LocalApiTokenBasedRequestHandler(
        dispatcher=dispatcher,
        bots_by_id=registry,
        handle_in_background=False,
    )
    request = MagicMock()
    request.match_info = {"bot_token": "123:runtime"}
    created = MagicMock()
    created.id = 123

    with patch("main.make_bot", return_value=created) as factory:
        resolved = await handler.resolve_bot(request)

    assert resolved is created
    assert registry == {123: created}
    factory.assert_called_once_with("123:runtime")


@patch("database.models.update_db", new_callable=MagicMock)
@patch("main.asyncio.run")
@patch("main.Dispatcher.start_polling", new_callable=MagicMock)
@patch("main.bot_config")
@patch("main.RedisStorage")
def test_main_polling_setup(
    mock_redis_storage,
    mock_config,
    mock_start_polling,
    mock_asyncio_run,
    mock_update_db,
):
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
    mock_update_db.assert_called_once_with()
    assert mock_asyncio_run.call_args_list[0].args[0] is mock_update_db.return_value


@patch("database.models.update_db", new_callable=MagicMock)
@patch("main.asyncio.run")
@patch("aiohttp.web.run_app")
@patch("main.bot_config")
@patch("main.RedisStorage")
def test_main_webhook_setup(
    mock_redis_storage,
    mock_config,
    mock_run_app,
    mock_asyncio_run,
    mock_update_db,
):
    """
    Test that main.py initializes in WEBHOOK mode (ENVIRONMENT=production).
    """
    from aiogram.fsm.storage.memory import MemoryStorage

    mock_redis_storage.from_url.return_value = MemoryStorage()

    # Setup mocks
    mock_config.main_bot_token = "123:main_token"
    mock_config.REDIS_URL = "redis://localhost:6379/0"
    mock_config.get_bot_settings.return_value = []  # No extra bots
    mock_config.OTHER_BOTS_PATH = "bot/{bot_token}"
    mock_config.SECRET_URL = "secret_path"
    mock_config.MAIN_BOT_PATH = "main"

    # Set Environment
    with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        main()

    # Verify web app run
    assert mock_run_app.called
    mock_update_db.assert_called_once_with()
    assert mock_asyncio_run.call_args_list[0].args[0] is mock_update_db.return_value

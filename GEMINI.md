# Project Overview

This project is a Python-based Telegram bot system that can operate in two modes: single bot and multi-bot. It's designed to provide support to users through Telegram. The system uses `aiogram` for Telegram Bot API interaction, `SQLAlchemy` and `TinyDB` for database operations, `redis` for session storage, and `pydantic` for data validation.

The project is structured into several modules:

*   `main.py`: The entry point for the multi-bot mode.
*   `single_bot.py`: The entry point for the single-bot mode.
*   `bot/`: Contains the bot's routers and dialogs.
*   `bot/customizations/`: Contains bot-specific logic and plugins.
*   `config/`: Contains the bot's configuration.
*   `data/`: Contains the bot's data, including a JSON file for bot configurations.
*   `database/`: Contains database-related modules.
*   `deploy/`: Contains deployment scripts.
*   `logs/`: Contains the bot's logs.

## Bot Customizations

The project supports a plugin system for bot-specific logic (e.g., custom buttons, text injections).
See [CONTRIBUTING.md](CONTRIBUTING.md) for a detailed guide on how to add new customizations.

## Building and Running

### Installation

1.  Clone the repository.
2.  Install the dependencies:

    ```bash
    uv sync
    ```

3.  Create a `.env` file in the root of the project. You can use `.env.sample` as a template.

### Running the bot

#### Single Bot Mode

This mode is ideal for small projects or for testing purposes.

To run the bot in single-bot mode, you need to set the following environment variables in your `.env` file:

```
SINGLE_BOT_TOKEN=<your_bot_token>
REDIS_URL=redis://localhost:6379/0
ADMIN_ID=<your_admin_id>
```

Then, run the following command:

```bash
uv run single_bot.py
```

#### Multi-Bot Mode

This mode allows you to run multiple bots at the same time.

To run the bot in multi-bot mode, you need to set the following environment variables in your `.env` file:

```
BOT_TOKEN=<your_main_bot_token>
REDIS_URL=redis://localhost:6379/0
ADMIN_ID=<your_admin_id>
BASE_URL=https://your-domain.com
```

You can run the multi-bot mode in two ways:

*   **Polling (for development):**

    ```bash
    uv run main.py
    ```

*   **Webhook (for production):**

    ```bash
    ENVIRONMENT=production uv run main.py
    ```

## Development Conventions

*   **Configuration:** The project uses a combination of environment variables and a JSON file (`data/bots.json`) for configuration. The `config/bot_config.py` module is responsible for loading and managing the configuration.
*   **Logging:** The project uses `loguru` for logging. Logs are stored in the `logs/` directory.
*   **Error Tracking:** The project uses `sentry-sdk` for error tracking.
*   **Database:** The project uses `SQLAlchemy` and `TinyDB` for database operations. The database files are stored in the `data/` directory.
*   **Dependencies:** The project's dependencies are managed by `uv` and listed in the `pyproject.toml` file.
*   **Typing:** The project uses type hints.
*   **Code Style:** The code is well-formatted and follows PEP 8 conventions.

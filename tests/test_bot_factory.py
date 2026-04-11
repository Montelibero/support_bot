"""Tests for config.bot_config.make_session / make_bot factory."""

import pytest

from config.bot_config import make_bot, make_session


def test_make_session_without_env_uses_cloud(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TELEGRAM_API_URL", raising=False)
    session = make_session()
    assert session.api.is_local is False
    assert "api.telegram.org" in session.api.base


def test_make_session_with_env_uses_local(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TELEGRAM_API_URL", "http://localhost:8081")
    session = make_session()
    assert session.api.is_local is True
    assert session.api.base.startswith("http://localhost:8081/")


async def test_make_bot_uses_factory_session(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TELEGRAM_API_URL", "http://bot-api.internal:8081")
    # dummy token — aiogram validates only the "<int>:<string>" shape
    bot = make_bot("123456:AAaaBBbbCCccDDdd")
    try:
        assert bot.session.api.is_local is True
        assert "bot-api.internal:8081" in bot.session.api.base
    finally:
        await bot.session.close()

import pytest
from unittest.mock import MagicMock

from bot.routers.supports import _resolve_agent_name, _no_name_error_text


@pytest.fixture
def local_settings():
    s = MagicMock()
    s.use_local_names = True
    s.local_names = {"111": "Алексей"}
    return s


@pytest.fixture
def global_settings():
    s = MagicMock()
    s.use_local_names = False
    s.local_names = {}
    return s


def test_resolve_local_name_found(local_settings):
    assert _resolve_agent_name(111, local_settings, user_info=None) == "Алексей"


def test_resolve_local_name_missing(local_settings):
    assert _resolve_agent_name(999, local_settings, user_info=None) is None


def test_resolve_global_name_found(global_settings):
    user_info = MagicMock()
    user_info.user_name = "GlobalName"
    assert _resolve_agent_name(111, global_settings, user_info=user_info) == "GlobalName"


def test_resolve_global_name_missing(global_settings):
    assert _resolve_agent_name(111, global_settings, user_info=None) is None


def test_local_mode_ignores_global_user_info(local_settings):
    user_info = MagicMock()
    user_info.user_name = "GlobalName"
    # user_id 999 not in local_names → None even though user_info exists
    assert _resolve_agent_name(999, local_settings, user_info=user_info) is None


def test_error_text_local_mode():
    text = _no_name_error_text(use_local_names=True)
    assert "локальные имена" in text.lower()
    assert "/myname" in text


def test_error_text_global_mode():
    text = _no_name_error_text(use_local_names=False)
    assert "глобальные имена" in text.lower()
    assert "/myname" in text

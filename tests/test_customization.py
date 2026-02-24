import pytest
from unittest.mock import MagicMock
from bot.customizations.registry import get_customization, register_customization
from bot.customizations.interface import AbstractBotCustomization
from bot.customizations.default import DefaultBotCustomization
from bot.customizations.test_customization import TestCustomization
from bot.customizations.helper import HelperCustomization
from bot.customizations.helper import _parse_helper_channel_message
from config.bot_config import SupportBotSettings


@pytest.fixture
def mock_bot_settings():
    return MagicMock(spec=SupportBotSettings)


@pytest.fixture
def mock_message():
    message = MagicMock()
    message.from_user = MagicMock()
    return message


def test_registry_default():
    # Test random ID returns Default
    customization = get_customization(999999)
    assert isinstance(customization, DefaultBotCustomization)


def test_registry_test_customization():
    # Test ID 123 returns TestCustomization
    customization = get_customization(123)
    assert isinstance(customization, TestCustomization)


def test_registry_helper_customization():
    # Test ID 5173438724 returns HelperCustomization
    customization = get_customization(5173438724)
    assert isinstance(customization, HelperCustomization)


@pytest.mark.asyncio
async def test_test_customization_logic(mock_message, mock_bot_settings):
    customization = TestCustomization()

    text = await customization.get_extra_text(
        mock_message.from_user, mock_message, mock_bot_settings
    )
    assert text == "\n[TEST MODE ACTIVATED]"

    markup = await customization.get_reply_markup(
        mock_message.from_user, mock_message, mock_bot_settings
    )
    assert markup is not None
    assert len(markup.inline_keyboard) == 1
    assert markup.inline_keyboard[0][0].text == "Test Button"


@pytest.mark.asyncio
async def test_default_customization_logic(mock_message, mock_bot_settings):
    customization = DefaultBotCustomization()

    text = await customization.get_extra_text(
        mock_message.from_user, mock_message, mock_bot_settings
    )
    assert text == ""

    markup = await customization.get_reply_markup(
        mock_message.from_user, mock_message, mock_bot_settings
    )
    assert markup is None


def test_parse_helper_channel_message_ack():
    parsed = _parse_helper_channel_message(
        "#helper #skynet command=ack status=ok op=taken url=https%3A%2F%2Ft.me%2Fc%2F1%2F2"
    )
    assert parsed is not None
    assert parsed["command"] == "ack"
    assert parsed["status"] == "ok"
    assert parsed["op"] == "taken"
    assert parsed["url"] == "https://t.me/c/1/2"


def test_parse_helper_channel_message_requires_helper_prefix():
    parsed = _parse_helper_channel_message(
        "#skynet #helper command=ack status=ok op=taken url=https%3A%2F%2Ft.me%2Fc%2F1%2F2"
    )
    assert parsed is None

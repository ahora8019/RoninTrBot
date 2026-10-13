import pytest

from app.core.config import ConfigurationError, load_settings


def test_loads_paper_defaults():
    settings = load_settings({"TELEGRAM_BOT_TOKEN": "test-token", "OWNER_IDS": "123, 456"})
    assert settings.owner_ids == (123, 456)
    assert settings.trading_mode == "paper"
    assert settings.live_trading_enabled is False
    assert settings.scan_interval_seconds == 300
    assert settings.market_intervals == ("15m", "1h", "4h")
    assert settings.minimum_signal_score == 70


def test_requires_telegram_token():
    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN is required"):
        load_settings({})


def test_rejects_invalid_owner_ids():
    with pytest.raises(ConfigurationError, match="numeric Telegram user IDs"):
        load_settings({"TELEGRAM_BOT_TOKEN": "test-token", "OWNER_IDS": "123,abc"})


def test_rejects_duplicate_owner_ids():
    with pytest.raises(ConfigurationError, match="must not contain duplicates"):
        load_settings({"TELEGRAM_BOT_TOKEN": "test-token", "OWNER_IDS": "123,123"})


def test_live_mode_is_not_available_in_foundation_stage():
    with pytest.raises(ConfigurationError, match="Only TRADING_MODE=paper"):
        load_settings({"TELEGRAM_BOT_TOKEN": "test-token", "TRADING_MODE": "live"})

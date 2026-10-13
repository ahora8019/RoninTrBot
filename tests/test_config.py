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


def test_requires_at_least_one_owner_id():
    with pytest.raises(ConfigurationError, match="OWNER_IDS must contain at least one owner"):
        load_settings({"TELEGRAM_BOT_TOKEN": "test-token"})


def test_rejects_invalid_owner_ids():
    with pytest.raises(ConfigurationError, match="numeric Telegram user IDs"):
        load_settings({"TELEGRAM_BOT_TOKEN": "test-token", "OWNER_IDS": "123,abc"})


def test_rejects_duplicate_owner_ids():
    with pytest.raises(ConfigurationError, match="must not contain duplicates"):
        load_settings({"TELEGRAM_BOT_TOKEN": "test-token", "OWNER_IDS": "123,123"})


def test_live_mode_is_not_available_in_foundation_stage():
    with pytest.raises(ConfigurationError, match="Only TRADING_MODE=paper"):
        load_settings({"TELEGRAM_BOT_TOKEN": "test-token", "OWNER_IDS": "123", "TRADING_MODE": "live"})


def test_loads_admin_ids_separately():
    settings = load_settings({
        "TELEGRAM_BOT_TOKEN": "test-token",
        "OWNER_IDS": "123",
        "ADMIN_IDS": "456,789",
    })
    assert settings.owner_ids == (123,)
    assert settings.admin_ids == (456, 789)


def test_rejects_overlap_between_owner_and_admin_ids():
    with pytest.raises(ConfigurationError, match="must not overlap"):
        load_settings({
            "TELEGRAM_BOT_TOKEN": "test-token",
            "OWNER_IDS": "123",
            "ADMIN_IDS": "123,456",
        })


def test_invalid_admin_ids_name_the_admin_setting():
    with pytest.raises(ConfigurationError, match="ADMIN_IDS must be comma-separated"):
        load_settings({
            "TELEGRAM_BOT_TOKEN": "test-token",
            "OWNER_IDS": "123",
            "ADMIN_IDS": "not-a-number",
        })

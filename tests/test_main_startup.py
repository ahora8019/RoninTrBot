import pytest

import app.main as main_module
from app.core.config import ConfigurationError, Settings


def _settings():
    return Settings(telegram_bot_token="123456:ABCDEF1234567890abcdef", owner_ids=(123,))


def test_invalid_config_exits_with_code_2(monkeypatch):
    def bad_load():
        raise ConfigurationError("missing token")

    monkeypatch.setattr(main_module, "load_settings", bad_load)
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 2


def test_startup_refuses_polling_when_webhook_is_configured(monkeypatch):
    monkeypatch.setattr(main_module, "load_settings", _settings)

    async def webhook_active(token):
        return True

    monkeypatch.setattr(main_module, "_webhook_is_configured", webhook_active)
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 3


def test_startup_fails_closed_when_webhook_precheck_crashes(monkeypatch):
    monkeypatch.setattr(main_module, "load_settings", _settings)

    def broken_probe(token):
        raise OSError("network unreachable")

    monkeypatch.setattr(main_module, "_webhook_is_configured", broken_probe)
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 3

from app.core.logging_setup import redact_secret


def test_redact_secret_never_returns_secret_value():
    secret = "super-secret-token"
    assert redact_secret(secret) == "[REDACTED]"
    assert secret not in redact_secret(secret)


def test_empty_secret_stays_empty():
    assert redact_secret("") == ""

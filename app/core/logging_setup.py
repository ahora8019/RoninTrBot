"""Minimal logging setup that avoids logging secret values."""

import logging


_REDACTED = "[REDACTED]"


def redact_secret(value: str) -> str:
    """Return a redacted marker for a non-empty secret without echoing it."""
    return _REDACTED if value else value


def configure_logging(level: int = logging.INFO) -> None:
    """Configure standard output logging with a predictable format."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

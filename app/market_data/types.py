"""Shared market-type definitions for isolated Spot and Futures data paths."""

from enum import Enum


class MarketType(str, Enum):
    """Market segment. Spot and Futures must never share an implicit fallback."""

    SPOT = "spot"
    FUTURES = "futures"


class MarketTypeUnavailableError(RuntimeError):
    """Raised when a market type has no explicitly configured data adapter."""

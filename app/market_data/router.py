"""Explicit router that keeps Spot and Futures market-data adapters isolated."""

from __future__ import annotations

from typing import Any

from app.market_data.types import MarketType, MarketTypeUnavailableError


class MarketDataRouter:
    """Route OHLCV requests only to the adapter configured for that market.

    A missing Futures adapter is a hard, clear error: it must never silently
    fall back to Spot data. This router is read-only and has no order methods.
    """

    def __init__(self, *, spot_client: Any, futures_client: Any | None = None) -> None:
        if spot_client is None:
            raise ValueError("spot_client is required")
        self._clients = {
            MarketType.SPOT: spot_client,
            MarketType.FUTURES: futures_client,
        }

    def fetch_ohlcv(self, market_type: MarketType | str, symbol: str, interval: str, limit: int = 200):
        """Fetch candles through the explicitly selected market adapter."""
        try:
            market = MarketType(market_type)
        except (TypeError, ValueError):
            raise ValueError("market_type must be 'spot' or 'futures'") from None

        client = self._clients[market]
        if client is None:
            raise MarketTypeUnavailableError(
                "LBank Futures market data is not configured yet; no Spot fallback was used."
            )
        fetch = getattr(client, "fetch_ohlcv", None)
        if not callable(fetch):
            raise MarketTypeUnavailableError(f"The {market.value} adapter does not support OHLCV data.")
        return fetch(symbol, interval, limit)

"""Unauthenticated LBank Spot market-data client.

Public endpoints only. This module never reads credentials or submits orders.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import time
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.market_scanner import MarketSnapshot, MarketType


class LBankAPIError(RuntimeError):
    """Raised when LBank returns an invalid or unsuccessful public response."""


@dataclass(frozen=True, slots=True)
class LBankTicker24h:
    symbol: str
    high: float
    low: float
    volume: float
    turnover: float
    last_price: float
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class LBankBookTicker:
    symbol: str
    bid_price: float
    ask_price: float
    bid_quantity: float
    ask_quantity: float


Transport = Callable[[str, float], Any]


def _http_get_json(url: str, timeout: float) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "RoninTrBot/1.0"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise LBankAPIError(f"unexpected_http_status:{response.status}")
        return json.loads(response.read().decode("utf-8"))


def _number(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise LBankAPIError(f"invalid_numeric_field:{field}") from exc
    if not math.isfinite(parsed):
        raise LBankAPIError(f"non_finite_field:{field}")
    return parsed


def _successful(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise LBankAPIError("response_not_object")
    result = payload.get("result")
    if result not in (True, "true", "TRUE", 1, "1"):
        raise LBankAPIError(f"api_error:{payload.get('error_code', 'unknown')}:{payload.get('msg', 'unsuccessful')}")
    if "data" not in payload:
        raise LBankAPIError("response_missing_data")
    return payload["data"]


class LBankSpotPublicClient:
    """Small public-only REST client for LBank Spot market data."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.lbank.info",
        timeout: float = 8.0,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("timeout_must_be_positive_and_finite")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport or _http_get_json
        self._clock = clock

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        return self._transport(f"{self.base_url}{path}{query}", self.timeout)

    def list_symbols(self) -> list[str]:
        data = _successful(self._get("/v2/currencyPairs.do"))
        if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
            raise LBankAPIError("invalid_currency_pairs_payload")
        return sorted({item.strip().lower() for item in data if item.strip()})

    def get_24h_tickers(self) -> list[LBankTicker24h]:
        data = _successful(self._get("/v2/ticker/24hr.do", {"symbol": "all"}))
        if not isinstance(data, list):
            raise LBankAPIError("invalid_ticker_list")
        tickers: list[LBankTicker24h] = []
        for row in data:
            if not isinstance(row, dict) or not isinstance(row.get("ticker"), dict):
                continue
            ticker = row["ticker"]
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                continue
            parsed = LBankTicker24h(
                symbol=symbol.strip().lower(),
                high=_number(ticker.get("high"), "high"),
                low=_number(ticker.get("low"), "low"),
                volume=_number(ticker.get("vol"), "vol"),
                turnover=_number(ticker.get("turnover"), "turnover"),
                last_price=_number(ticker.get("latest"), "latest"),
                timestamp_ms=int(_number(row.get("timestamp", self._clock() * 1000), "timestamp")),
            )
            if parsed.low <= 0 or parsed.high < parsed.low or parsed.last_price <= 0:
                continue
            if min(parsed.volume, parsed.turnover) < 0:
                continue
            tickers.append(parsed)
        return tickers

    def get_book_ticker(self, symbol: str) -> LBankBookTicker:
        normalized = _normalize_symbol(symbol)
        data = _successful(self._get("/v2/supplement/ticker/bookTicker.do", {"symbol": normalized}))
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            raise LBankAPIError("invalid_book_ticker_payload")
        parsed_symbol = data.get("symbol", normalized)
        bid = _number(data.get("bidPrice"), "bidPrice")
        ask = _number(data.get("askPrice"), "askPrice")
        bid_qty = _number(data.get("bidQty"), "bidQty")
        ask_qty = _number(data.get("askQty"), "askQty")
        if not isinstance(parsed_symbol, str) or bid <= 0 or ask <= 0 or ask < bid or min(bid_qty, ask_qty) < 0:
            raise LBankAPIError("invalid_order_book_values")
        return LBankBookTicker(parsed_symbol.lower(), bid, ask, bid_qty, ask_qty)

    def to_snapshot(
        self,
        ticker: LBankTicker24h,
        book: LBankBookTicker,
        *,
        max_age_seconds: float = 120.0,
    ) -> MarketSnapshot:
        """Convert validated exchange data into scanner's normalized format."""
        if ticker.symbol != book.symbol:
            raise LBankAPIError("ticker_symbol_mismatch")
        if max_age_seconds <= 0 or not math.isfinite(max_age_seconds):
            raise ValueError("max_age_seconds_must_be_positive_and_finite")
        midpoint = (book.bid_price + book.ask_price) / 2.0
        spread_bps = ((book.ask_price - book.bid_price) / midpoint) * 10_000.0
        age = max(0.0, self._clock() - ticker.timestamp_ms / 1000.0)
        volatility_pct = ((ticker.high - ticker.low) / ticker.last_price) * 100.0
        trend = "unknown"
        return MarketSnapshot(
            symbol=ticker.symbol.replace("_", "").upper(),
            market_type=MarketType.SPOT,
            price=ticker.last_price,
            volume_quote=ticker.turnover,
            spread_bps=spread_bps,
            volatility_pct=volatility_pct,
            data_age_seconds=age,
            trend=trend,
        )


def _normalize_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise ValueError("symbol_must_be_string")
    normalized = symbol.strip().lower().replace("/", "_").replace("-", "_")
    if "_" not in normalized and normalized.endswith("usdt"):
        normalized = f"{normalized[:-4]}_usdt"
    if not normalized or any(not (char.isalnum() or char == "_") for char in normalized):
        raise ValueError("invalid_symbol")
    return normalized

"""Async, Workers-compatible public LBank OHLCV transport.

Inject Cloudflare js.fetch (or a fake async fetcher in tests). This module
intentionally exposes no authenticated account endpoints or order methods.
"""
from __future__ import annotations
import asyncio, json, re, time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

BASE_URL = "https://api.lbank.info/v2/kline.do"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
INTERVALS = {"15m": ("minute15", 900), "1h": ("hour1", 3600), "4h": ("hour4", 14400)}
_SYMBOL_RE = re.compile(r"^[a-z0-9]+_[a-z0-9]+$")

class AsyncMarketDataError(RuntimeError):
    pass

class AsyncMarketDataHTTPError(AsyncMarketDataError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"LBank HTTP request failed with status {status}")

@dataclass(frozen=True, slots=True)
class AsyncCandle:
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise AsyncMarketDataError(f"Invalid {field} in LBank candle") from None
    if not result.is_finite():
        raise AsyncMarketDataError(f"Non-finite {field} in LBank candle")
    return result

def _parse_row(row: Any) -> AsyncCandle:
    if not isinstance(row, (list, tuple)) or len(row) != 6:
        raise AsyncMarketDataError("Each LBank candle must contain exactly 6 fields")
    stamp = _decimal(row[0], "timestamp")
    if stamp != stamp.to_integral_value() or stamp <= 0:
        raise AsyncMarketDataError("Candle timestamp must be a positive integer")
    o, h, low, close, volume = (
        _decimal(row[1], "open"), _decimal(row[2], "high"),
        _decimal(row[3], "low"), _decimal(row[4], "close"),
        _decimal(row[5], "volume"),
    )
    if min(o, h, low, close) <= 0 or volume < 0:
        raise AsyncMarketDataError("Candle OHLC prices must be positive and volume non-negative")
    if h < max(o, close, low) or low > min(o, close, h):
        raise AsyncMarketDataError("Inconsistent OHLC values")
    return AsyncCandle(int(stamp), o, h, low, close, volume)

class AsyncLBankMarketDataClient:
    """Bounded async fetch/retry client for public Spot candles."""
    def __init__(self, fetcher: Callable[..., Any], *, timeout_seconds: float = 8,
                 max_retries: int = 2, clock=time.time, sleep=asyncio.sleep):
        if timeout_seconds <= 0 or not 0 <= max_retries <= 3:
            raise ValueError("Invalid timeout or retry limit")
        self._fetch, self._timeout, self._retries = fetcher, timeout_seconds, max_retries
        self._clock, self._sleep = clock, sleep

    async def fetch_ohlcv(self, symbol: str, interval: str, limit: int = 200) -> list[AsyncCandle]:
        symbol = symbol.strip().lower()
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError("symbol must use LBank pair format, e.g. btc_usdt")
        if interval not in INTERVALS:
            raise ValueError("Unsupported interval; use 15m, 1h, or 4h")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1999:
            raise ValueError("limit must be an integer between 1 and 1999")
        api_interval, seconds = INTERVALS[interval]
        now = int(self._clock())
        url = (f"{BASE_URL}?symbol={symbol}&type={api_interval}&size={limit + 1}"
               f"&time={now - limit * seconds}")
        payload = None
        for attempt in range(self._retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._fetch(url, {"headers": {"Accept": "application/json"}}),
                    timeout=self._timeout,
                )
                status = int(response.status)
                if status < 200 or status >= 300:
                    raise AsyncMarketDataHTTPError(status)
                headers = getattr(response, "headers", None)
                length = headers.get("content-length") if headers is not None else None
                if length and int(length) > MAX_RESPONSE_BYTES:
                    raise AsyncMarketDataError("LBank response exceeded maximum size")
                raw = await response.text()
                if len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
                    raise AsyncMarketDataError("LBank response exceeded maximum size")
                payload = json.loads(raw)
                break
            except AsyncMarketDataHTTPError as exc:
                if (exc.status != 429 and exc.status < 500) or attempt >= self._retries:
                    raise
            except Exception as exc:
                if attempt >= self._retries:
                    raise AsyncMarketDataError(f"LBank request failed: {type(exc).__name__}") from None
            await self._sleep(min(0.25 * (2 ** attempt), 2.0))
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            if str(payload.get("error_code", 0)) not in {"0", "None"}:
                raise AsyncMarketDataError("LBank returned an API error")
            if payload.get("result") not in (True, "true", "True", None):
                raise AsyncMarketDataError("LBank did not report success")
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise AsyncMarketDataError("LBank response is missing candle data")
        else:
            raise AsyncMarketDataError("Unexpected LBank response format")
        if not rows:
            raise AsyncMarketDataError("LBank returned no candle data")
        candles = sorted((_parse_row(row) for row in rows), key=lambda candle: candle.timestamp)
        stamps = [c.timestamp for c in candles]
        if len(stamps) != len(set(stamps)):
            raise AsyncMarketDataError("LBank returned duplicate timestamps")
        if any(stamp % seconds != 0 or stamp > now + seconds for stamp in stamps):
            raise AsyncMarketDataError("LBank returned invalid candle timestamps")
        if any(b - a != seconds for a, b in zip(stamps, stamps[1:])):
            raise AsyncMarketDataError("LBank returned a gap in candle series")
        if now - candles[-1].timestamp > seconds * 3:
            raise AsyncMarketDataError("LBank latest candle is stale")
        return candles[-limit:]

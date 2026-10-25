"""Read-only LBank spot OHLCV adapter with strict payload validation.

This module intentionally exposes no authenticated account or order endpoints.
The public V2 K-line endpoint is documented at https://www.lbank.com/docs/.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.client import IncompleteRead
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://api.lbank.info"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_LIMIT = 1999  # LBank returns at most 2000 rows; one slot is reserved for a
                  # window-boundary candle so `size` never silently truncates.
INTERVALS: dict[str, tuple[str, int]] = {
    "15m": ("minute15", 15 * 60),
    "1h": ("hour1", 60 * 60),
    "4h": ("hour4", 4 * 60 * 60),
}
_SYMBOL_RE = re.compile(r"^[a-z0-9]+_[a-z0-9]+$")


class MarketDataError(RuntimeError):
    """Base error for market-data retrieval and validation failures."""


class MarketDataHTTPError(MarketDataError):
    """An HTTP response that was not successful."""

    def __init__(self, status: int, message: str = "") -> None:
        self.status = status
        super().__init__(f"LBank HTTP request failed with status {status}" + (f": {message}" if message else ""))


class MarketDataAPIError(MarketDataError):
    """LBank returned an API-level error or an unexpected envelope."""


class MarketDataValidationError(MarketDataError):
    """Market data was missing, malformed, stale, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Candle:
    """Normalized OHLCV candle; timestamp is Unix time in seconds."""

    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


Transport = Callable[[str, Mapping[str, str], float], Any]


def _default_transport(url: str, params: Mapping[str, str], timeout: float) -> Any:
    request_url = f"{url}?{urlencode(params)}"
    request = Request(request_url, headers={"Accept": "application/json", "User-Agent": "RoninTrBot/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise MarketDataError("LBank response exceeded the maximum allowed size")
            return json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(256).decode("utf-8", errors="replace")
        except Exception:
            pass
        raise MarketDataHTTPError(exc.code, detail) from None
    except (URLError, TimeoutError, IncompleteRead, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError(f"LBank market-data request failed: {type(exc).__name__}") from None


def _decimal(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise MarketDataValidationError(f"Invalid {field} value in LBank candle") from None
    if not number.is_finite():
        raise MarketDataValidationError(f"Non-finite {field} value in LBank candle")
    return number


class LBankMarketDataClient:
    """Fetch and validate public LBank spot candles; never submits orders."""

    def __init__(
        self,
        *,
        transport: Transport = _default_transport,
        timeout_seconds: float = 8.0,
        min_request_interval_seconds: float = 0.25,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if min_request_interval_seconds < 0:
            raise ValueError("min_request_interval_seconds must not be negative")
        if max_retries < 0 or max_retries > 5:
            raise ValueError("max_retries must be between 0 and 5")
        self._transport = transport
        self._timeout = timeout_seconds
        self._min_interval = min_request_interval_seconds
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._monotonic = monotonic
        self._rate_lock = threading.Lock()
        self._last_request_at: float | None = None
        self.last_skipped_rows: int = 0

    def _respect_rate_limit(self) -> None:
        with self._rate_lock:
            now = self._monotonic()
            if self._last_request_at is not None:
                wait = self._min_interval - (now - self._last_request_at)
                if wait > 0:
                    self._sleep(wait)
            self._last_request_at = self._monotonic()

    def _request(self, params: Mapping[str, str]) -> Any:
        for attempt in range(self._max_retries + 1):
            self._respect_rate_limit()
            try:
                return self._transport(f"{BASE_URL}/v2/kline.do", params, self._timeout)
            except MarketDataHTTPError as exc:
                retryable = exc.status == 429 or 500 <= exc.status <= 599
                if not retryable or attempt >= self._max_retries:
                    raise
                self._sleep(min(0.25 * (2**attempt), 2.0))
            except MarketDataError:
                if attempt >= self._max_retries:
                    raise
                self._sleep(min(0.25 * (2**attempt), 2.0))
        raise MarketDataError("LBank request exhausted retries")

    def fetch_ohlcv(self, symbol: str, interval: str, limit: int = 200,
                    *, on_invalid: str = "raise") -> list[Candle]:
        """Fetch up to `limit` latest candles, ordered oldest-to-newest and validated.

        Supported intervals are 15m, 1h, and 4h. The LBank V2 `time` parameter is
        a *start* timestamp: the endpoint returns rows from the candle containing
        `time` forward, capped at `size` rows counting from the start. The fetch
        window therefore starts `limit * interval` seconds back; the response is
        trimmed to the most recent `limit` rows. The final candle may still be
        forming (its close is provisional); callers needing closed candles must
        drop it when `candle.timestamp + interval_seconds > now`.

        ``on_invalid="raise"`` (default) fails closed on any structurally invalid
        row (non-numeric fields, inconsistent OHLC, ...). ``on_invalid="skip"``
        omits such rows instead; this exists for research/backfill where one
        vendor glitch must not destroy a long series, and must never be used to
        silently tolerate bad data in any execution path. The skipped rows are
        observable via the ``last_skipped_rows`` instance attribute (reset on
        every call), and series continuity is relaxed only up to that many
        missing candles; larger unexplained gaps still fail closed.

        This is strictly public, read-only market data; no API key or trading
        permission is used.
        """
        if on_invalid not in ("raise", "skip"):
            raise ValueError("on_invalid must be 'raise' or 'skip'")
        symbol = symbol.strip().lower()
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError("symbol must use LBank pair format, e.g. btc_usdt")
        if interval not in INTERVALS:
            raise ValueError(f"Unsupported interval {interval!r}; use one of {tuple(INTERVALS)}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}")

        now = int(self._clock())
        api_interval, interval_seconds = INTERVALS[interval]
        payload = self._request({
            "symbol": symbol,
            "type": api_interval,
            "size": str(limit + 1),
            "time": str(now - limit * interval_seconds),
        })
        rows = self._extract_rows(payload)
        if not rows:
            raise MarketDataValidationError("LBank returned no candle data")

        candles: list[Candle] = []
        self.last_skipped_rows = 0
        for row in rows:
            try:
                candles.append(self._parse_row(row))
            except MarketDataValidationError:
                if on_invalid == "raise":
                    raise
                self.last_skipped_rows += 1
        if not candles:
            raise MarketDataValidationError("LBank returned no usable candle data")
        candles.sort(key=lambda candle: candle.timestamp)
        timestamps = [candle.timestamp for candle in candles]
        if len(timestamps) != len(set(timestamps)):
            raise MarketDataValidationError("LBank returned duplicate candle timestamps")
        if any(timestamp > now + interval_seconds for timestamp in timestamps):
            raise MarketDataValidationError("LBank returned a candle timestamp in the future")
        if any(timestamp % interval_seconds != 0 for timestamp in timestamps):
            raise MarketDataValidationError(f"LBank returned a candle not aligned to the {interval} interval")
        if on_invalid == "skip":
            # A skipped invalid row creates a discontinuity by design; tolerate
            # gaps only up to the number of skipped rows so unrelated vendor
            # gaps still fail closed.
            missing = sum(max((later - earlier) // interval_seconds - 1, 0)
                          for earlier, later in zip(timestamps, timestamps[1:]))
            if missing > self.last_skipped_rows:
                raise MarketDataValidationError(
                    f"LBank returned a gap in the {interval} candle series")
        elif any(later - earlier != interval_seconds
                 for earlier, later in zip(timestamps, timestamps[1:])):
            raise MarketDataValidationError(f"LBank returned a gap in the {interval} candle series")
        if now - candles[-1].timestamp > interval_seconds * 3:
            raise MarketDataValidationError(f"LBank latest {interval} candle is stale")
        return candles[-limit:]

    @staticmethod
    def _extract_rows(payload: Any) -> list[Any]:
        if isinstance(payload, list):
            # Some documented/legacy responses return the rows directly.
            return payload
        if not isinstance(payload, dict):
            raise MarketDataAPIError("Unexpected LBank response format")
        result = payload.get("result")
        error_code = payload.get("error_code", 0)
        if str(error_code) not in {"0", "None"}:
            raise MarketDataAPIError(f"LBank API returned error code {error_code}")
        if result not in (True, "true", "True", None):
            raise MarketDataAPIError("LBank did not report a successful result")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise MarketDataAPIError("LBank response is missing the candle data array")
        return rows

    @staticmethod
    def _parse_row(row: Any) -> Candle:
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise MarketDataValidationError("Each LBank candle must contain exactly 6 fields")
        try:
            timestamp_decimal = _decimal(row[0], "timestamp")
            if timestamp_decimal != timestamp_decimal.to_integral_value():
                raise MarketDataValidationError("Candle timestamp must be an integer in seconds")
            timestamp = int(timestamp_decimal)
        except (OverflowError, ValueError):
            raise MarketDataValidationError("Invalid candle timestamp") from None
        if timestamp <= 0:
            raise MarketDataValidationError("Candle timestamp must be positive")

        open_price = _decimal(row[1], "open")
        high = _decimal(row[2], "high")
        low = _decimal(row[3], "low")
        close = _decimal(row[4], "close")
        volume = _decimal(row[5], "volume")
        if min(open_price, high, low, close) <= 0:
            raise MarketDataValidationError("OHLC prices must be positive")
        if volume < 0:
            raise MarketDataValidationError("Candle volume must not be negative")
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            raise MarketDataValidationError("Inconsistent OHLC values")
        return Candle(timestamp, open_price, high, low, close, volume)

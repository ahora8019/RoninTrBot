"""Deterministic, read-only indicators and explainable multi-timeframe signals.

This module does not access the network, read credentials, or place orders.
All prices are converted to Decimal to avoid binary floating-point drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

SUPPORTED_TIMEFRAMES = ("15m", "1h", "4h")
SIGNAL_THRESHOLD = 70


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field} must be a finite decimal") from None
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result


@dataclass(frozen=True, slots=True)
class Candle:
    """One immutable OHLCV candle; timestamp is Unix seconds."""
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int) or self.timestamp < 0:
            raise ValueError("timestamp must be a non-negative Unix-second integer")
        for name in ("open", "high", "low", "close", "volume"):
            value = _decimal(getattr(self, name), name)
            object.__setattr__(self, name, value)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.low > self.high:
            raise ValueError("low must not exceed high")


def validate_candles(candles: Iterable[Candle], *, min_count: int = 1) -> tuple[Candle, ...]:
    """Return candles in order, rejecting duplicate or non-increasing timestamps."""
    result = tuple(candles)
    if len(result) < min_count:
        raise ValueError(f"at least {min_count} candles are required")
    previous = -1
    for candle in result:
        if not isinstance(candle, Candle):
            raise TypeError("candles must contain Candle records")
        if candle.timestamp <= previous:
            raise ValueError("candle timestamps must be strictly increasing")
        previous = candle.timestamp
    return result


def sma(values: Sequence[Decimal], period: int) -> Decimal:
    if period < 1 or len(values) < period:
        raise ValueError("not enough values for SMA period")
    return sum(values[-period:], Decimal("0")) / Decimal(period)


def rsi(closes: Sequence[Decimal], period: int = 14) -> Decimal:
    """Simple-window RSI; returns 50 for a flat window, 100 for gains only."""
    if period < 1 or len(closes) < period + 1:
        raise ValueError("not enough closes for RSI period")
    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains = sum((max(change, Decimal("0")) for change in changes), Decimal("0"))
    losses = sum((-min(change, Decimal("0")) for change in changes), Decimal("0"))
    if losses == 0:
        return Decimal("50") if gains == 0 else Decimal("100")
    average_gain = gains / Decimal(period)
    average_loss = losses / Decimal(period)
    return Decimal("100") - Decimal("100") / (Decimal("1") + average_gain / average_loss)


def atr(candles: Sequence[Candle], period: int = 14) -> Decimal:
    """Simple average true range over the latest period true ranges."""
    if period < 1 or len(candles) < period + 1:
        raise ValueError("not enough candles for ATR period")
    ranges: list[Decimal] = []
    for previous, current in zip(candles[-period - 1:-1], candles[-period:]):
        ranges.append(max(current.high - current.low,
                          abs(current.high - previous.close),
                          abs(current.low - previous.close)))
    return sum(ranges, Decimal("0")) / Decimal(period)


@dataclass(frozen=True, slots=True)
class TimeframeAnalysis:
    timeframe: str
    direction: str
    score: int
    close: Decimal
    sma_fast: Decimal
    sma_slow: Decimal
    rsi: Decimal
    atr: Decimal
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Signal:
    market_type: str
    symbol: str
    direction: str
    score: int
    eligible: bool
    timeframe_analyses: tuple[TimeframeAnalysis, ...]
    reasons: tuple[str, ...]


def analyze_timeframe(timeframe: str, candles: Iterable[Candle]) -> TimeframeAnalysis:
    """Score one timeframe with transparent trend and momentum rules."""
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {', '.join(SUPPORTED_TIMEFRAMES)}")
    raw_data = tuple(candles)
    if len(raw_data) < 51:
        raise ValueError("not enough candles; at least 51 are required for indicator warm-up")
    data = validate_candles(raw_data, min_count=51)
    closes = [c.close for c in data]
    fast, slow = sma(closes, 9), sma(closes, 21)
    momentum, volatility = rsi(closes, 14), atr(data, 14)
    score, reasons, direction = 0, [], "neutral"

    if fast > slow and closes[-1] > slow:
        direction, score = "long", score + 40
        reasons.append("close and SMA(9) are above SMA(21)")
    elif fast < slow and closes[-1] < slow:
        direction, score = "short", score + 40
        reasons.append("close and SMA(9) are below SMA(21)")
    else:
        reasons.append("moving-average trend is mixed")

    if direction == "long" and Decimal("50") < momentum < Decimal("70"):
        score += 30
        reasons.append("RSI(14) supports upward momentum without being overbought")
    elif direction == "short" and Decimal("30") < momentum < Decimal("50"):
        score += 30
        reasons.append("RSI(14) supports downward momentum without being oversold")
    elif direction == "long" and momentum >= Decimal("70"):
        score += 10
        reasons.append("RSI(14) is overbought; upward momentum is extended")
    elif direction == "short" and momentum <= Decimal("30"):
        score += 10
        reasons.append("RSI(14) is oversold; downward momentum is extended")
    else:
        reasons.append("RSI(14) does not confirm the trend")

    if volatility > 0 and closes[-1] > 0 and volatility / closes[-1] <= Decimal("0.05"):
        score += 20
        reasons.append("ATR(14) is at or below 5% of close")
    else:
        reasons.append("ATR(14) is zero or elevated relative to close")

    return TimeframeAnalysis(timeframe, direction, min(score, 100), closes[-1],
                             fast, slow, momentum, volatility, tuple(reasons))


def analyze_signal(*, symbol: str, market_type: str,
                   candles_by_timeframe: dict[str, Iterable[Candle]],
                   threshold: int = SIGNAL_THRESHOLD) -> Signal:
    """Combine 15m/1h/4h results; signals are informational and never execute trades."""
    normalized_market = market_type.strip().lower() if isinstance(market_type, str) else ""
    if normalized_market not in {"spot", "futures"}:
        raise ValueError("market_type must be 'spot' or 'futures'")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be non-empty")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 100:
        raise ValueError("threshold must be an integer from 0 to 100")
    if set(candles_by_timeframe) != set(SUPPORTED_TIMEFRAMES):
        raise ValueError("candles_by_timeframe must contain exactly 15m, 1h, and 4h")

    analyses = tuple(analyze_timeframe(tf, candles_by_timeframe[tf]) for tf in SUPPORTED_TIMEFRAMES)
    reasons = tuple(f"{item.timeframe}: {item.direction}, score {item.score}/100" for item in analyses)
    directions = {item.direction for item in analyses}
    if len(directions) != 1 or "neutral" in directions:
        return Signal(normalized_market, symbol.strip().upper(), "neutral", 0, False,
                      analyses, reasons + ("timeframes disagree or at least one trend is neutral",))

    direction = analyses[0].direction
    score = sum(item.score for item in analyses) // len(analyses)
    eligible = score >= threshold
    final_reason = (f"all timeframes agree; average score meets threshold {threshold}"
                    if eligible else f"average score is below threshold {threshold}")
    return Signal(normalized_market, symbol.strip().upper(), direction, score, eligible,
                  analyses, reasons + (final_reason,))

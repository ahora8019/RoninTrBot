"""Deterministic closed-candle strategy evaluation for paper trading.

Signals are suggestions only; this module never submits orders. It deliberately
returns NO_TRADE when the sample is too short or the setup is ambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Sequence


class StrategyName(str, Enum):
    EMA_PULLBACK = "EMA_Pullback"
    RSI_REVERSAL = "RSI_Reversal"
    VOLUME_BREAKOUT = "Volume_Breakout"
    NONE = "NO_TRADE"


class SignalAction(str, Enum):
    PAPER_LONG = "PAPER_LONG"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


@dataclass(frozen=True, slots=True)
class StrategySignal:
    action: SignalAction
    strategy: StrategyName
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyPolicy:
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    atr_period: int = 14
    breakout_lookback: int = 20
    volume_multiplier: float = 1.8
    minimum_reward_risk: float = 1.5
    max_stop_atr: float = 3.0


def _ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("insufficient_values_for_ema")
    alpha = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _rsi(closes: Sequence[float], period: int) -> float:
    if len(closes) < period + 1:
        raise ValueError("insufficient_values_for_rsi")
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = changes[-period:]
    gains = sum(max(change, 0.0) for change in recent) / period
    losses = sum(max(-change, 0.0) for change in recent) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + gains / losses))


def _atr(candles: Sequence[Candle], period: int) -> float:
    if len(candles) < period + 1:
        raise ValueError("insufficient_values_for_atr")
    true_ranges = []
    for previous, current in zip(candles[-period-1:-1], candles[-period:]):
        true_ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return sum(true_ranges) / period


def evaluate_closed_candles(
    candles: Sequence[Candle],
    policy: StrategyPolicy = StrategyPolicy(),
) -> StrategySignal:
    """Evaluate only closed, chronologically ordered candles; long-only v1."""
    required = max(
        policy.ema_slow + 2,
        policy.rsi_period + 1,
        policy.atr_period + 1,
        policy.breakout_lookback + 1,
    )
    if len(candles) < required:
        return _no_trade("insufficient_candles")
    if any(not candle.is_closed for candle in candles):
        return _no_trade("contains_unclosed_candle")
    if any(candles[i].timestamp >= candles[i + 1].timestamp for i in range(len(candles) - 1)):
        return _no_trade("candles_not_strictly_chronological")
    for candle in candles:
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if any(not math.isfinite(value) for value in values):
            return _no_trade("non_finite_candle")
        if min(candle.open, candle.high, candle.low, candle.close) <= 0 or candle.volume < 0:
            return _no_trade("invalid_candle_values")
        if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(candle.open, candle.close, candle.high):
            return _no_trade("invalid_ohlc_relationship")

    closes = [c.close for c in candles]
    latest = candles[-1]
    atr = _atr(candles, policy.atr_period)
    if atr <= 0:
        return _no_trade("zero_atr")
    fast_now = _ema(closes, policy.ema_fast)
    slow_now = _ema(closes, policy.ema_slow)
    fast_previous = _ema(closes[:-1], policy.ema_fast)
    rsi = _rsi(closes, policy.rsi_period)

    # Support is based only on candles preceding the current closed candle.
    previous = candles[:-1]
    breakout_window = previous[-policy.breakout_lookback:]
    resistance = max(c.high for c in breakout_window)
    mean_volume = sum(c.volume for c in previous[-policy.breakout_lookback:]) / policy.breakout_lookback

    candidate: tuple[StrategyName, int, tuple[str, ...]] | None = None
    if (
        fast_now > slow_now
        and fast_previous <= fast_now
        and latest.close >= fast_now
        and latest.close - latest.low <= atr * 1.5
    ):
        candidate = (StrategyName.EMA_PULLBACK, 74, ("ema_trend_aligned", "closed_candle_pullback"))
    elif rsi <= 30 and latest.close > latest.open:
        candidate = (StrategyName.RSI_REVERSAL, 72, ("rsi_oversold", "bullish_reversal_candle"))
    elif latest.close > resistance and mean_volume > 0 and latest.volume >= mean_volume * policy.volume_multiplier:
        candidate = (StrategyName.VOLUME_BREAKOUT, 76, ("close_above_prior_resistance", "volume_confirmation"))

    if candidate is None:
        return _no_trade("no_validated_long_setup")
    strategy, score, reasons = candidate
    stop = latest.close - atr * 1.5
    if stop <= 0 or latest.close - stop > atr * policy.max_stop_atr:
        return _no_trade("stop_distance_outside_policy")
    target = latest.close + (latest.close - stop) * policy.minimum_reward_risk
    return StrategySignal(
        action=SignalAction.PAPER_LONG,
        strategy=strategy,
        entry=latest.close,
        stop_loss=stop,
        take_profit=target,
        score=score,
        reasons=("paper_only", "closed_candles_only", *reasons),
    )


def _no_trade(reason: str) -> StrategySignal:
    return StrategySignal(
        action=SignalAction.NO_TRADE,
        strategy=StrategyName.NONE,
        entry=None,
        stop_loss=None,
        take_profit=None,
        score=0,
        reasons=(reason,),
    )

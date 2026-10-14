from decimal import Decimal

import pytest

from app.strategy.indicators import (
    Candle, SIGNAL_THRESHOLD, analyze_signal, analyze_timeframe, atr, rsi, sma,
)


def candles_trending(direction="up", count=60):
    result = []
    for i in range(count):
        close = Decimal(100 + i) if direction == "up" else Decimal(200 - i)
        result.append(Candle(
            timestamp=1_700_000_000 + i * 900,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=Decimal("10"),
        ))
    return result


def test_candle_converts_values_to_decimal_and_is_immutable():
    candle = Candle(1, "10", "12", "9", "11", "4")
    assert candle.close == Decimal("11")
    with pytest.raises((AttributeError, TypeError)):
        candle.close = Decimal("12")


@pytest.mark.parametrize("kwargs", [
    {"timestamp": -1, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 1},
    {"timestamp": 1, "open": 10, "high": 9, "low": 8, "close": 11, "volume": 1},
    {"timestamp": 1, "open": 10, "high": 12, "low": 9, "close": 11, "volume": -1},
    {"timestamp": 1, "open": 10, "high": "NaN", "low": 9, "close": 11, "volume": 1},
])
def test_invalid_candle_rejected(kwargs):
    with pytest.raises(ValueError):
        Candle(**kwargs)


def test_sma_rsi_atr_calculations_are_defined():
    assert sma([Decimal("1"), Decimal("2"), Decimal("3")], 2) == Decimal("2.5")
    assert rsi([Decimal(i) for i in range(16)], 14) == Decimal("100")
    data = candles_trending()
    assert atr(data, 14) > 0


def test_indicators_reject_insufficient_history():
    with pytest.raises(ValueError, match="not enough"):
        analyze_timeframe("15m", candles_trending(count=20))


def test_duplicate_timestamps_rejected():
    data = candles_trending(count=3)
    with pytest.raises(ValueError, match="strictly increasing"):
        from app.strategy.indicators import validate_candles
        validate_candles([data[0], data[1], data[1]])


def test_uptrend_signal_is_explainable_and_thresholded():
    data = {tf: candles_trending("up") for tf in ("15m", "1h", "4h")}
    signal = analyze_signal(symbol="btc_usdt", market_type="spot", candles_by_timeframe=data)
    assert signal.direction == "long"
    assert signal.score >= SIGNAL_THRESHOLD
    assert signal.eligible is True
    assert signal.symbol == "BTC_USDT"
    assert len(signal.timeframe_analyses) == 3
    assert all(item.reasons for item in signal.timeframe_analyses)


def test_downtrend_signal_is_short_and_market_is_kept_separate():
    data = {tf: candles_trending("down") for tf in ("15m", "1h", "4h")}
    signal = analyze_signal(symbol="eth_usdt", market_type="futures", candles_by_timeframe=data)
    assert signal.market_type == "futures"
    assert signal.direction == "short"
    assert signal.eligible is True


def test_timeframe_disagreement_fails_closed():
    data = {
        "15m": candles_trending("up"),
        "1h": candles_trending("down"),
        "4h": candles_trending("up"),
    }
    signal = analyze_signal(symbol="BTC_USDT", market_type="spot", candles_by_timeframe=data)
    assert signal.direction == "neutral"
    assert signal.score == 0
    assert signal.eligible is False


@pytest.mark.parametrize("market", ["margin", "", None])
def test_invalid_market_type_rejected(market):
    data = {tf: candles_trending() for tf in ("15m", "1h", "4h")}
    with pytest.raises(ValueError, match="market_type"):
        analyze_signal(symbol="BTC_USDT", market_type=market, candles_by_timeframe=data)


def test_all_three_timeframes_are_required():
    with pytest.raises(ValueError, match="exactly 15m, 1h, and 4h"):
        analyze_signal(symbol="BTC_USDT", market_type="spot",
                       candles_by_timeframe={"15m": candles_trending()})


def test_rsi_flat_and_exact_mixed_windows():
    flat = [Decimal("10")] * 16
    assert rsi(flat, 14) == Decimal("50")
    # Alternating +1/-1 changes over the last 4 closes: RS = 1 -> RSI = 50.
    mixed = [Decimal("10"), Decimal("11"), Decimal("10"), Decimal("11"), Decimal("10")]
    assert rsi(mixed, 4) == Decimal("50")


def test_atr_exact_value():
    data = [Candle(1, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("1")),
            Candle(2, Decimal("101"), Decimal("103"), Decimal("100"), Decimal("102"), Decimal("1")),
            Candle(3, Decimal("102"), Decimal("104"), Decimal("101"), Decimal("103"), Decimal("1"))]
    # True ranges: max(3, 2, 1) = 3 twice -> ATR(2) = 3 exactly.
    assert atr(data, 2) == Decimal("3")


def test_indicator_error_paths_for_insufficient_values():
    with pytest.raises(ValueError, match="not enough values for SMA"):
        sma([Decimal("1")], 2)
    with pytest.raises(ValueError, match="not enough closes for RSI"):
        rsi([Decimal("1")], 14)
    with pytest.raises(ValueError, match="not enough candles for ATR"):
        atr([], 14)


def test_score_threshold_boundary_is_inclusive():
    data = {tf: candles_trending("up") for tf in ("15m", "1h", "4h")}
    at = analyze_signal(symbol="btc_usdt", market_type="spot", candles_by_timeframe=data, threshold=70)
    above = analyze_signal(symbol="btc_usdt", market_type="spot", candles_by_timeframe=data, threshold=71)

    assert at.score == 70  # 40 trend + 10 extended RSI + 20 contained ATR
    assert at.eligible is True   # score == threshold passes (inclusive)
    assert above.eligible is False
    assert any("below threshold" in r for r in above.reasons)


@pytest.mark.parametrize("bad", [True, -1, 101])
def test_analyze_signal_rejects_invalid_threshold(bad):
    data = {tf: candles_trending("up") for tf in ("15m", "1h", "4h")}
    with pytest.raises(ValueError, match="threshold"):
        analyze_signal(symbol="btc_usdt", market_type="spot",
                       candles_by_timeframe=data, threshold=bad)

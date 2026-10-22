import pytest

from app.core.strategy_engine import (
    Candle,
    SignalAction,
    StrategyName,
    evaluate_closed_candles,
)


def rising_candles(count=30):
    result = []
    price = 100.0
    for index in range(count):
        open_price = price
        price += 0.4
        result.append(Candle(
            timestamp=index + 1,
            open=open_price,
            high=price + 0.2,
            low=open_price - 0.2,
            close=price,
            volume=100.0,
        ))
    return result


def test_short_history_fails_closed():
    assert evaluate_closed_candles(rising_candles(10)).action is SignalAction.NO_TRADE


def test_unclosed_candle_is_rejected():
    candles = rising_candles()
    candles[-1] = Candle(**{**candles[-1].__dict__, "is_closed": False})
    result = evaluate_closed_candles(candles)
    assert result.action is SignalAction.NO_TRADE
    assert result.reasons == ("contains_unclosed_candle",)


def test_non_chronological_candles_are_rejected():
    candles = rising_candles()
    candles[-1] = Candle(**{**candles[-1].__dict__, "timestamp": candles[-2].timestamp})
    assert "candles_not_strictly_chronological" in evaluate_closed_candles(candles).reasons


def test_invalid_ohlc_is_rejected():
    candles = rising_candles()
    candles[-1] = Candle(
        timestamp=candles[-1].timestamp, open=110, high=105,
        low=109, close=110, volume=100,
    )
    assert "invalid_ohlc_relationship" in evaluate_closed_candles(candles).reasons


def test_no_signal_does_not_create_entry_or_order():
    candles = [
        Candle(index + 1, 100, 100.2, 99.8, 100, 100)
        for index in range(30)
    ]
    result = evaluate_closed_candles(candles)
    assert result.action is SignalAction.NO_TRADE
    assert result.entry is None


def test_signal_if_generated_has_positive_risk_reward_and_paper_only():
    result = evaluate_closed_candles(rising_candles(30))
    if result.action is SignalAction.PAPER_LONG:
        assert result.strategy in {
            StrategyName.EMA_PULLBACK,
            StrategyName.RSI_REVERSAL,
            StrategyName.VOLUME_BREAKOUT,
        }
        assert result.stop_loss < result.entry < result.take_profit
        assert "paper_only" in result.reasons

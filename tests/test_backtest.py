from decimal import Decimal

import pytest

from app.strategy.indicators import Candle
from app.validation.backtest import BacktestConfig, TradeIntent, run_backtest


def candle(ts, o, h, l, c):
    return Candle(ts, Decimal(str(o)), Decimal(str(h)), Decimal(str(l)),
                  Decimal(str(c)), Decimal("10"))


def test_signal_enters_at_next_open_and_hits_long_target():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 106, 99, 104)]
    intent = TradeIntent(1, "long", Decimal("1"), Decimal("2"), Decimal("4"))
    result = run_backtest(data, {1: intent}, BacktestConfig(
        initial_equity=Decimal("1000"), fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    assert result.trade_count == 1
    trade = result.trades[0]
    assert trade.entry_timestamp == 2
    assert trade.exit_timestamp == 3
    assert trade.exit_reason == "target"
    assert trade.net_pnl == Decimal("4")
    assert result.final_equity == Decimal("1004")


def test_stop_wins_when_stop_and_target_touch_same_candle():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 105, 95, 100)]
    intent = TradeIntent(1, "long", Decimal("1"), Decimal("2"), Decimal("4"))
    result = run_backtest(data, {1: intent}, BacktestConfig(
        fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    assert result.trades[0].exit_reason == "stop_first_both_touched"
    assert result.trades[0].net_pnl == Decimal("-2")


def test_fees_and_slippage_reduce_long_pnl():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 106, 99, 104)]
    result = run_backtest(data, {1: TradeIntent(1, "long", Decimal("1"),
        Decimal("2"), Decimal("4"))}, BacktestConfig(
        initial_equity=Decimal("1000"), fee_rate=Decimal("0.01"),
        slippage_rate=Decimal("0.01")))
    assert result.trades[0].net_pnl < Decimal("4")
    assert result.total_fees > 0
    assert result.final_equity == result.initial_equity + result.net_pnl


def test_total_fees_include_entry_and_exit_fees():
    # Regression: total_fees previously accumulated entry fees only.
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 106, 99, 104)]
    result = run_backtest(data, {1: TradeIntent(1, "long", Decimal("1"),
        Decimal("2"), Decimal("4"))}, BacktestConfig(
        initial_equity=Decimal("1000"), fee_rate=Decimal("0.01"),
        slippage_rate=Decimal("0")))
    expected = sum(trade.fees for trade in result.trades)
    assert result.total_fees == expected
    # Deterministic expectation: entry fee 100*1*0.01 = 1, exit fee 104*1*0.01 = 1.04.
    assert result.total_fees == Decimal("2.04")


def test_spot_short_intent_is_rejected():
    with pytest.raises(ValueError, match="spot short"):
        TradeIntent(1, "short", Decimal("1"), Decimal("2"), Decimal("4"))


def test_short_requires_explicit_futures_market():
    intent = TradeIntent(1, "short", Decimal("1"), Decimal("2"), Decimal("4"),
                         market_type="FUTURES")
    assert intent.market_type == "futures"
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 101, 93, 96)]  # high stays below the 102 stop; low touches the 96 target
    result = run_backtest(data, {1: intent}, BacktestConfig(
        initial_equity=Decimal("1000"), fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    assert result.trades[0].exit_reason == "target"
    assert result.trades[0].net_pnl == Decimal("4")
    assert result.final_equity == result.initial_equity + result.net_pnl


def test_invalid_intent_market_type_is_rejected():
    with pytest.raises(ValueError, match="market_type"):
        TradeIntent(1, "long", Decimal("1"), Decimal("2"), Decimal("4"),
                    market_type="margin")


def test_rejects_signal_on_final_candle():
    data = [candle(1, 100, 101, 99, 100), candle(2, 100, 101, 99, 100)]
    intent = TradeIntent(2, "long", Decimal("1"), Decimal("2"), Decimal("4"))
    with pytest.raises(ValueError):
        run_backtest(data, {2: intent})


def test_config_rejects_invalid_costs():
    with pytest.raises(ValueError):
        BacktestConfig(fee_rate=Decimal("1"))
    with pytest.raises(ValueError):
        BacktestConfig(slippage_rate=Decimal("-0.1"))


def test_invalid_intent_quantity_is_rejected():
    with pytest.raises(ValueError):
        TradeIntent(1, "long", Decimal("0"), Decimal("2"), Decimal("4"))


def test_invalid_intent_record_fails_with_type_error():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100)]
    with pytest.raises(TypeError, match="TradeIntent"):
        run_backtest(data, {1: object()})


def test_boolean_intent_timestamp_key_is_rejected():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100)]
    intent = TradeIntent(1, "long", Decimal("1"), Decimal("2"), Decimal("4"))
    with pytest.raises(ValueError, match="integer Unix timestamps"):
        run_backtest(data, {True: intent})


def test_drawdown_includes_unrealized_intratrade_loss():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 101, 89, 90),
            candle(4, 100, 105, 95, 100)]
    result = run_backtest(data, {1: TradeIntent(
        1, "long", Decimal("1"), Decimal("20"), Decimal("40"))},
        BacktestConfig(initial_equity=Decimal("1000"), fee_rate=Decimal("0"),
                       slippage_rate=Decimal("0")))
    assert result.trade_count == 1
    assert result.trades[0].net_pnl == Decimal("0")
    assert result.max_drawdown_fraction >= Decimal("0.009")


def test_intent_key_must_match_its_signal_timestamp():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 101, 99, 100)]
    with pytest.raises(ValueError, match="must match a candle timestamp and its key"):
        run_backtest(data, {1: TradeIntent(2, "long", Decimal("1"), Decimal("2"), Decimal("4"))})


def test_signals_are_ignored_while_a_position_is_open():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 101, 99, 100),
            candle(4, 100, 101, 99, 100),
            candle(5, 100, 101, 99, 101)]
    intents = {1: TradeIntent(1, "long", Decimal("1"), Decimal("20"), Decimal("40")),
               3: TradeIntent(3, "long", Decimal("1"), Decimal("20"), Decimal("40"))}
    result = run_backtest(data, intents, BacktestConfig(
        fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    assert result.trade_count == 1
    assert result.trades[0].signal_timestamp == 1
    assert result.trades[0].exit_reason == "end_of_data"


def test_long_stop_gap_exits_at_the_open():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 96, 97, 95, 96)]  # opens below the 98 stop
    result = run_backtest(data, {1: TradeIntent(1, "long", Decimal("1"), Decimal("2"), Decimal("4"))},
                          BacktestConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    assert result.trades[0].exit_reason == "stop_gap"
    assert result.trades[0].exit_price == Decimal("96")
    assert result.trades[0].net_pnl == Decimal("-4")


def test_long_target_gap_exits_at_the_open():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 106, 107, 105, 106)]  # opens above the 104 target
    result = run_backtest(data, {1: TradeIntent(1, "long", Decimal("1"), Decimal("2"), Decimal("4"))},
                          BacktestConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    assert result.trades[0].exit_reason == "target_gap"
    assert result.trades[0].net_pnl == Decimal("6")


def test_short_futures_target_gap_exits_at_the_open():
    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 94, 95, 93, 94)]  # opens below the 96 target
    intent = TradeIntent(1, "short", Decimal("1"), Decimal("2"), Decimal("4"),
                         market_type="futures")
    result = run_backtest(data, {1: intent},
                          BacktestConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    assert result.trades[0].exit_reason == "target_gap"
    assert result.trades[0].net_pnl == Decimal("6")

from decimal import Decimal

import pytest

from app.strategy.indicators import Candle
from app.validation.backtest import BacktestConfig, TradeIntent
from app.validation.walkforward import evaluate_walk_forward


def candle(ts, o, h, l, c):
    return Candle(ts, Decimal(str(o)), Decimal(str(h)), Decimal(str(l)),
                  Decimal(str(c)), Decimal("10"))


def flat(ts):
    return candle(ts, 100, 101, 99, 100)


# 12 candles: flat except ts=11, where a long target (104) is touched.
DATA = [flat(t) for t in range(1, 11)] + [candle(11, 100, 106, 99, 100), flat(12)]


def make_factory(seen):
    """Deterministic factory: signal at the first timestamp of each test window."""

    def factory(train, test_timestamps):
        seen.append((tuple(c.timestamp for c in train), test_timestamps))
        if len(test_timestamps) < 2:
            return {}
        return {test_timestamps[0]: TradeIntent(test_timestamps[0], "long", Decimal("1"),
                                                Decimal("2"), Decimal("4"))}

    return factory


def test_walk_forward_chains_equity_and_aggregates_trades():
    seen = []
    report = evaluate_walk_forward(DATA, factory=make_factory(seen),
                                   train_size=4, test_size=4, step=4,
                                   config=BacktestConfig(fee_rate=Decimal("0"),
                                                         slippage_rate=Decimal("0")))

    assert len(report.windows) == 2
    # Window 0 (test ts 5-8): entry at ts 6, no hit, end-of-data exit -> 0 PnL.
    # Window 1 (test ts 9-12): entry at ts 10, target hit at ts 11 -> +4.
    assert report.final_equity == Decimal("1004")
    assert report.net_pnl == Decimal("4")
    assert report.total_trades == 2
    assert report.win_count == 1
    assert report.loss_count == 0
    assert report.max_drawdown_fraction == Decimal("0")
    assert report.windows[1].initial_equity == report.windows[0].final_equity


def test_factory_never_sees_test_prices_or_future_timestamps():
    seen = []
    report = evaluate_walk_forward(DATA, factory=make_factory(seen),
                                   train_size=4, test_size=4, step=4,
                                   config=BacktestConfig(fee_rate=Decimal("0"),
                                                         slippage_rate=Decimal("0")))
    assert report.total_trades == 2
    for train_ts, test_ts in seen:
        assert max(train_ts) < min(test_ts)  # train strictly precedes test


def test_walk_forward_carries_chained_drawdown_across_windows():
    # Long stop (distance 2) guaranteed to trigger a -2 loss within each window.
    def losing_factory(train, test_timestamps):
        if len(test_timestamps) < 2:
            return {}
        return {test_timestamps[0]: TradeIntent(test_timestamps[0], "long", Decimal("1"),
                                                Decimal("0.5"), Decimal("40"))}

    data = [flat(t) for t in range(1, 13)]
    report = evaluate_walk_forward(data, factory=losing_factory,
                                   train_size=4, test_size=4, step=4,
                                   config=BacktestConfig(fee_rate=Decimal("0"),
                                                         slippage_rate=Decimal("0")))
    assert report.net_pnl < 0
    assert report.max_drawdown_fraction > 0
    # Chained drawdown is at least as large as any single window's.
    assert all(report.max_drawdown_fraction >= w.max_drawdown_fraction
               for w in report.windows)


def test_walk_forward_rejects_intent_on_final_test_candle():
    def bad_factory(train, test_timestamps):
        return {test_timestamps[-1]: TradeIntent(test_timestamps[-1], "long",
                                                 Decimal("1"), Decimal("2"), Decimal("4"))}

    with pytest.raises(ValueError):
        evaluate_walk_forward(DATA, factory=bad_factory, train_size=4, test_size=4, step=4)


def test_walk_forward_fails_closed_with_too_little_data():
    with pytest.raises(ValueError, match="no full walk-forward window"):
        evaluate_walk_forward(DATA[:6], factory=make_factory([]),
                              train_size=5, test_size=4)


def test_equity_marks_cover_every_candle_and_end_at_final_equity():
    from app.validation.backtest import run_backtest

    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 106, 99, 104)]
    intent = TradeIntent(1, "long", Decimal("1"), Decimal("2"), Decimal("4"))
    result = run_backtest(data, {1: intent}, BacktestConfig(
        initial_equity=Decimal("1000"), fee_rate=Decimal("0"), slippage_rate=Decimal("0")))

    assert len(result.equity_marks) == len(data)
    assert result.equity_marks[0] == Decimal("1000")
    assert result.equity_marks[-1] == result.final_equity == Decimal("1004")


def test_equity_marks_reproduce_max_drawdown_exactly():
    from app.validation.backtest import run_backtest

    data = [candle(1, 100, 101, 99, 100),
            candle(2, 100, 101, 99, 100),
            candle(3, 100, 101, 89, 90),
            candle(4, 100, 105, 95, 100)]
    result = run_backtest(data, {1: TradeIntent(1, "long", Decimal("1"),
        Decimal("20"), Decimal("40"))}, BacktestConfig(
        initial_equity=Decimal("1000"), fee_rate=Decimal("0"), slippage_rate=Decimal("0")))

    peak = result.initial_equity
    recomputed = Decimal("0")
    for mark in result.equity_marks:
        peak = max(peak, mark)
        if peak > 0:
            recomputed = max(recomputed, (peak - mark) / peak)
    assert recomputed == result.max_drawdown_fraction == Decimal("0.01")

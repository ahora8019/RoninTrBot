from decimal import Decimal

import pytest

from app.strategy.indicators import Candle
from app.validation.backtest import BacktestConfig, TradeIntent
from app.validation.walkforward import evaluate_walk_forward, evaluate_walk_forward_steps


def candle(ts, price):
    price = Decimal(str(price))
    return Candle(ts, price, price + Decimal("2"), price - Decimal("2"), price, Decimal("5"))


def flat(n, price=100):
    return tuple(candle(i, price) for i in range(n))


def intent(ts, qty="0.5"):
    return TradeIntent(ts, "long", Decimal(qty), Decimal("4"), Decimal("8"))


def test_step_factory_never_sees_future_candles():
    seen_histories: list[tuple[int, ...]] = []
    last_needed = 20
    data = flat(last_needed)

    def factory(history, timestamp):
        assert history[-1].timestamp == timestamp, "history must end at the signal candle"
        seen_histories.append(tuple(c.timestamp for c in history))
        return intent(timestamp)

    report = evaluate_walk_forward_steps(data, factory=factory, train_size=8, test_size=4,
                                         config=BacktestConfig(fee_rate=Decimal("0"),
                                                               slippage_rate=Decimal("0")))
    assert report.total_trades > 0
    # Every recorded history is a strict prefix of the data (no future leakage).
    all_ts = tuple(c.timestamp for c in data)
    for hist in seen_histories:
        assert list(hist) == list(all_ts[: len(hist)])
        assert len(hist) < len(all_ts)


def test_step_factory_per_window_histories_include_train_then_test_prefix():
    data = flat(14)
    lengths: list[int] = []

    def factory(history, timestamp):
        lengths.append(len(history))
        return None

    evaluate_walk_forward_steps(data, factory=factory, train_size=4, test_size=3, step=3,
                                anchored=True)
    # 3 full windows (w3 would need 3 more candles than available -> dropped).
    # History includes the signal candle: train 4 -> lens 5,6 ; anchored: 8,9 ; 11,12
    assert lengths == [5, 6, 8, 9, 11, 12]


def test_step_protocol_matches_batch_protocol_for_time_only_policy():
    data = flat(18)
    config = BacktestConfig(fee_rate=Decimal("0.001"), slippage_rate=Decimal("0.001"))

    # Policy uses timestamps only (no prices) so it is expressible in both protocols.
    def step_factory(history, timestamp):
        return intent(timestamp) if timestamp % 3 == 0 else None

    def batch_factory(train, timestamps):
        # Batch factories must skip the final test timestamp of each window:
        # run_backtest rejects an intent keyed to the last candle it is given.
        return {ts: intent(ts) for ts in timestamps[:-1] if ts % 3 == 0}

    by_steps = evaluate_walk_forward_steps(data, factory=step_factory, train_size=6,
                                           test_size=4, config=config)
    by_batch = evaluate_walk_forward(data, factory=batch_factory, train_size=6,
                                     test_size=4, config=config)
    assert by_steps.final_equity == by_batch.final_equity
    assert by_steps.total_trades == by_batch.total_trades == 3
    assert by_steps.max_drawdown_fraction == by_batch.max_drawdown_fraction


def test_step_protocol_chains_equity_across_windows():
    # Rising prices; intent on every test candle -> chained equity must reflect growth.
    data = tuple(candle(i, 100 + i) for i in range(16))
    report = evaluate_walk_forward_steps(
        data, factory=lambda h, ts: intent(ts), train_size=4, test_size=4,
        config=BacktestConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    assert len(report.windows) == 3
    equities = [report.initial_equity] + [w.final_equity for w in report.windows]
    assert all(b > a for a, b in zip(equities, equities[1:]))
    for w_prev, w_next in zip(report.windows, report.windows[1:]):
        assert w_next.initial_equity == w_prev.final_equity


def test_step_protocol_skips_last_test_timestamp():
    data = flat(10)
    called: list[int] = []
    evaluate_walk_forward_steps(
        data, factory=lambda h, ts: called.append(ts) or None,
        train_size=6, test_size=4)
    # One window only; test ts are 6,7,8,9 -> factory consulted for 6,7,8 not 9.
    assert called == [6, 7, 8]


def test_step_protocol_requires_full_window():
    with pytest.raises(ValueError, match="no full walk-forward window"):
        evaluate_walk_forward_steps(flat(9), factory=lambda h, ts: None,
                                    train_size=6, test_size=4)

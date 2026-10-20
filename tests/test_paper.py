from decimal import Decimal

import pytest

from app.paper.portfolio import PaperConfig, PaperEngine
from app.strategy.indicators import Candle
from app.validation.backtest import TradeIntent


def candle(ts, o, h, l, c, v="10"):
    return Candle(ts, Decimal(str(o)), Decimal(str(h)), Decimal(str(l)),
                  Decimal(str(c)), Decimal(str(v)))


def long_intent(ts, qty="1", stop="2", target="4"):
    return TradeIntent(ts, "long", Decimal(qty), Decimal(stop), Decimal(target))


def test_fill_happens_at_next_open_with_adverse_slippage():
    engine = PaperEngine(PaperConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0.01")))
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    assert engine.position is None  # not filled on the signal candle

    engine.step(candle(2, 100, 101, 99.5, 100))  # low 99.5 stays above the 99 stop
    assert engine.position is not None
    assert engine.position.entry == Decimal("101")  # 100 * 1.01
    assert any(d.action == "entry_filled" for d in engine.decisions)


def test_entry_rejected_when_notional_exceeds_paper_cash():
    engine = PaperEngine(PaperConfig(initial_cash=Decimal("1000"),
                                     fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1, qty="500"))
    engine.step(candle(2, 100, 101, 99, 100))
    assert engine.position is None
    assert engine.cash == Decimal("1000")
    assert any(d.action == "entry_rejected" for d in engine.decisions)


def test_stop_first_both_touched_exit_and_accounting():
    engine = PaperEngine(PaperConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    engine.step(candle(2, 100, 101, 99, 100))
    engine.step(candle(3, 100, 105, 95, 100))  # both 98 stop and 104 target touched
    trade = engine.trades[0]
    assert trade.exit_reason == "stop_first_both_touched"
    assert trade.net_pnl == Decimal("-2")
    assert engine.cash == Decimal("998")


def test_fees_accounting_matches_backtest_canonical_fixture():
    # Same fixture as the backtest: entry fee 1.00 + exit fee 1.04 = 2.04 total.
    engine = PaperEngine(PaperConfig(initial_cash=Decimal("1000"),
                                     fee_rate=Decimal("0.01"), slippage_rate=Decimal("0")))
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    engine.step(candle(2, 100, 101, 99, 100))
    engine.step(candle(3, 100, 106, 99, 104))
    assert engine.total_fees == Decimal("2.04")
    assert engine.cash == Decimal("1001.96")
    assert engine.trades[0].net_pnl == Decimal("1.96")


def test_halt_blocks_entries_with_recorded_reason():
    engine = PaperEngine(PaperConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    engine.halt("risk breach")
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    assert engine.position is None
    assert engine.pending is None
    rejected = [d for d in engine.decisions if d.action == "entry_rejected"]
    assert rejected and "risk breach" in rejected[-1].reason


def test_resume_and_signal_ignored_while_position_open():
    engine = PaperEngine(PaperConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    engine.halt("pause")
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    rejected = [d for d in engine.decisions if d.action == "entry_rejected"]
    assert rejected and "pause" in rejected[-1].reason

    engine.resume()
    engine.step(candle(2, 100, 101, 99, 100), long_intent(2))
    engine.step(candle(3, 100, 101, 99, 100))
    assert engine.position is not None

    engine.step(candle(4, 100, 101, 99, 100), long_intent(4))
    assert any(d.action == "signal_ignored" for d in engine.decisions)


def test_finish_closes_at_final_close_with_slippage():
    engine = PaperEngine(PaperConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0.01")))
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    engine.step(candle(2, 100, 101, 99.5, 100))  # entry at 101; low 99.5 stays above the 99 stop
    decision = engine.finish(candle(3, 110, 111, 109, 110))
    assert decision is not None and decision.action == "closed_final"
    trade = engine.trades[0]
    assert trade.exit_reason == "end_of_data"
    # exit = 110 * 0.99 = 108.9 -> net (108.9 - 101) * 1
    assert trade.net_pnl == Decimal("7.9")


def test_equity_at_marks_unrealized_adverse_exit():
    engine = PaperEngine(PaperConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    engine.step(candle(2, 100, 101, 99, 100))  # entry 100
    assert engine.equity_at(Decimal("110")) == Decimal("1010")
    assert engine.equity_at(Decimal("100")) == Decimal("1000")


def test_futures_short_roundtrip_with_profit():
    engine = PaperEngine(PaperConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    short = TradeIntent(1, "short", Decimal("1"), Decimal("2"), Decimal("4"),
                        market_type="futures")
    engine.step(candle(1, 100, 101, 99, 100), short)
    engine.step(candle(2, 100, 101, 99, 100))  # entry 100, stop 102, target 96
    engine.step(candle(3, 100, 101, 93, 96))   # low touches target; high below stop
    assert engine.trades[0].exit_reason == "target"
    assert engine.trades[0].net_pnl == Decimal("4")


def test_snapshot_restore_roundtrip_behaves_like_continuous_run():
    config = PaperConfig(initial_cash=Decimal("1000"), fee_rate=Decimal("0.01"),
                         slippage_rate=Decimal("0.01"))
    steps = [candle(1, 100, 101, 99, 100), candle(2, 100, 101, 99, 100),
             candle(3, 100, 106, 99, 104)]

    continuous = PaperEngine(config)
    continuous.step(steps[0], long_intent(1))
    continuous.step(steps[1])
    continuous.step(steps[2])

    restart = PaperEngine(config)
    restart.step(steps[0], long_intent(1))
    restart.step(steps[1])
    restored = PaperEngine.restore(restart.snapshot())
    restored.step(steps[2])

    assert restored.cash == continuous.cash
    assert restored.total_fees == continuous.total_fees
    assert len(restored.trades) == len(continuous.trades) == 1
    assert restored.trades[0].net_pnl == continuous.trades[0].net_pnl
    assert len(restored.decisions) == len(continuous.decisions)


def test_snapshot_is_json_safe_and_restoration_fail_closes():
    import json

    engine = PaperEngine()
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    engine.step(candle(2, 100, 101, 99, 100))
    json.dumps(engine.snapshot())  # must be serializable
    with pytest.raises((ValueError, TypeError)):
        PaperEngine.restore({"cash": "1000"})


def test_out_of_order_candles_are_rejected():
    engine = PaperEngine()
    engine.step(candle(2, 100, 101, 99, 100))
    with pytest.raises(ValueError, match="strictly increasing"):
        engine.step(candle(2, 100, 101, 99, 100))


def test_paper_engine_has_no_exchange_or_network_dependency():
    import inspect
    import app.paper.portfolio as module_source_container
    import app.paper

    source = inspect.getsource(app.paper) + inspect.getsource(module_source_container)
    for forbidden in ("urllib", "requests", "httpx", "socket", "telegram"):
        assert forbidden not in source, f"paper engine must stay hermetic (found {forbidden!r})"


def test_snapshot_schema_version_gating():
    from app.paper.portfolio import SNAPSHOT_SCHEMA_VERSION

    engine = PaperEngine()
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    snapshot = engine.snapshot()
    assert snapshot["schema_version"] == SNAPSHOT_SCHEMA_VERSION

    # Pre-versioned snapshots (v1) restore for backward compatibility.
    legacy = {k: v for k, v in snapshot.items() if k != "schema_version"}
    PaperEngine.restore(legacy)

    # Future versions fail closed.
    newer = dict(snapshot, schema_version=SNAPSHOT_SCHEMA_VERSION + 1)
    import pytest as _pytest
    with _pytest.raises(ValueError, match="newer than supported"):
        PaperEngine.restore(newer)

    # Malformed version values fail closed.
    with _pytest.raises(ValueError, match="positive integer"):
        PaperEngine.restore(dict(snapshot, schema_version="2"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("step_index", True),
        ("last_timestamp", True),
        ("halt_reason", []),
    ],
)
def test_restore_rejects_invalid_control_state_types(field, value):
    snapshot = PaperEngine().snapshot()
    snapshot[field] = value
    with pytest.raises(ValueError, match="snapshot"):
        PaperEngine.restore(snapshot)


def test_restore_rejects_invalid_position_direction():
    engine = PaperEngine()
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    engine.step(candle(2, 100, 101, 99, 100))
    snapshot = engine.snapshot()
    snapshot["position"]["direction"] = "sideways"

    with pytest.raises(ValueError, match="position direction"):
        PaperEngine.restore(snapshot)


def test_restore_rejects_non_positive_position_quantity():
    engine = PaperEngine()
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    engine.step(candle(2, 100, 101, 99, 100))
    snapshot = engine.snapshot()
    snapshot["position"]["quantity"] = "0"

    with pytest.raises(ValueError, match="quantity"):
        PaperEngine.restore(snapshot)


def test_restore_rejects_position_and_pending_entry_together():
    engine = PaperEngine()
    engine.step(candle(1, 100, 101, 99, 100), long_intent(1))
    engine.step(candle(2, 100, 101, 99, 100))
    snapshot = engine.snapshot()
    snapshot["pending"] = {
        "signal_timestamp": 3,
        "direction": "long",
        "market_type": "spot",
        "quantity": "1",
        "stop_distance": "2",
        "target_distance": "4",
    }

    with pytest.raises(ValueError, match="both an open position"):
        PaperEngine.restore(snapshot)

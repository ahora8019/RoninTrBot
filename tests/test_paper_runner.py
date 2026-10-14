from decimal import Decimal

from app.paper.portfolio import PaperConfig, PaperEngine
from app.paper.runner import run_paper_session
from app.strategy.indicators import Candle
from app.strategy.policy import TrendPolicy


def rising(n=90, start=100.0, step=0.4):
    out = []
    for i in range(n):
        close = Decimal(str(round(start + step * i, 6)))
        out.append(Candle(i, close - Decimal("0.02"), close + Decimal("0.05"),
                          close - Decimal("0.05"), close, Decimal("5")))
    return tuple(out)


def headroom_policy():
    # max_notional_fraction 0.95 leaves entry-fee headroom: engines reject
    # notional+fee above the equity cap (parity: backtest does the same).
    from app.risk.engine import RiskLimits
    return TrendPolicy(market_type="futures",
                       limits=RiskLimits(max_notional_fraction=Decimal("0.95")))


def test_session_processes_every_candle_and_sizes_through_risk():
    report = run_paper_session(rising(), policy=headroom_policy())
    assert report.candles_processed == 90
    assert report.trade_count >= 1
    scheduled = [d for d in report.decisions if d.action == "entry_scheduled"]
    assert scheduled, "uptrend must schedule entries after warm-up"
    # Warm-up: no decision before candle index 51 can carry an entry_scheduled.
    assert all(d.index >= 51 or d.action != "entry_scheduled" for d in report.decisions)


def test_fill_rejection_is_recorded_when_policy_leaves_no_fee_headroom():
    # Default max_notional_fraction=1 with cap binding -> every fill rejected,
    # rejection recorded (engine truthfulness, mirrors the backtest's skip).
    engine = PaperEngine(PaperConfig(fee_rate=Decimal("0.001"), slippage_rate=Decimal("0")))
    report = run_paper_session(rising(), policy=TrendPolicy(market_type="futures"),
                               engine=engine)
    assert report.trade_count == 0
    assert any(d.action == "entry_rejected" and "insufficient" in d.reason
               for d in report.decisions)


def test_session_is_deterministic():
    r1 = run_paper_session(rising(), policy=headroom_policy())
    r2 = run_paper_session(rising(), policy=headroom_policy())
    assert r1 == r2


def test_pre_halted_engine_blocks_all_entries_and_finishes_flat():
    engine = PaperEngine(PaperConfig(initial_cash=Decimal("1000"),
                                     fee_rate=Decimal("0.001"),
                                     slippage_rate=Decimal("0.001")))
    engine.halt("pre-session kill switch")
    report = run_paper_session(rising(), policy=headroom_policy(), engine=engine)
    assert report.halted
    assert report.trade_count == 0
    assert report.final_equity == report.initial_equity == Decimal("1000")
    assert any("kill switch" in d.reason for d in report.decisions
               if d.action == "entry_rejected")


def test_final_equity_includes_end_of_data_close():
    engine = PaperEngine(PaperConfig(fee_rate=Decimal("0"), slippage_rate=Decimal("0")))
    report = run_paper_session(rising(), policy=headroom_policy(), engine=engine)
    assert engine.position is None  # finish() closed the residual position
    assert report.decisions[-1].action == "closed_final"
    assert report.final_equity == engine.cash
    assert report.final_equity > report.initial_equity  # rising trend, no costs


def test_mark_validation_rejects_unknown_mark():
    import pytest
    with pytest.raises(ValueError, match="close"):
        run_paper_session(rising(60), mark="mid")

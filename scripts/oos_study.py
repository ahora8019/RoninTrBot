#!/usr/bin/env python3
"""Out-of-sample study on real LBank history (public, read-only market data).

Fetches closed candles via the LBank adapter, then runs, with REAL data:
  1. walk-forward evaluation of the risk-sized trend policy (step protocol),
  2. an isolated paper session on the final test window.

No credentials, no orders, no paid services. Network use is limited to the
public kline endpoint inside app.market_data.lbank (read-only). Results are
printed as JSON; this script is a thin CLI over 100%-unit-tested components,
so the mechanics are verified offline; the data fetch is the live part.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.market_data.lbank import INTERVALS, LBankMarketDataClient  # noqa: E402
from app.strategy.indicators import Candle as StrategyCandle  # noqa: E402
from app.paper.portfolio import PaperConfig  # noqa: E402
from app.paper.runner import run_paper_session  # noqa: E402
from app.risk.engine import RiskLimits  # noqa: E402
from app.strategy.policy import TrendPolicy, trend_intent  # noqa: E402
from app.validation.backtest import BacktestConfig  # noqa: E402
from app.validation.walkforward import evaluate_walk_forward_steps, WalkForwardReport  # noqa: E402


def make_step_factory(policy: TrendPolicy, config: BacktestConfig):
    def factory(history, timestamp):
        return trend_intent(history, equity=config.initial_equity, policy=policy)
    return factory


def report_to_json(report: WalkForwardReport) -> dict:
    return {
        "windows": [
            {"index": w.index, "train_size": w.train_size, "test_size": w.test_size,
             "initial_equity": str(w.initial_equity), "final_equity": str(w.final_equity),
             "net_pnl": str(w.net_pnl), "trades": w.trade_count,
             "max_drawdown_fraction": str(w.max_drawdown_fraction)}
            for w in report.windows
        ],
        "initial_equity": str(report.initial_equity),
        "final_equity": str(report.final_equity),
        "net_pnl": str(report.net_pnl),
        "total_trades": report.total_trades,
        "win_count": report.win_count,
        "loss_count": report.loss_count,
        "total_fees": str(report.total_fees),
        "max_drawdown_fraction": str(report.max_drawdown_fraction),
        "notes": list(report.notes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="btc_usdt")
    parser.add_argument("--market", default="futures", choices=["spot", "futures"])
    parser.add_argument("--interval", default="1h", choices=sorted(INTERVALS))
    parser.add_argument("--limit", type=int, default=1500)
    parser.add_argument("--train", type=int, default=400)
    parser.add_argument("--test", type=int, default=100)
    parser.add_argument("--fee", default="0.001")
    parser.add_argument("--slippage", default="0.0005")
    args = parser.parse_args()

    client = LBankMarketDataClient()
    # Research/backfill context: skip (counted) structurally invalid vendor rows
    # instead of losing the whole series; the execution default stays fail-closed.
    fetched = client.fetch_ohlcv(args.symbol, args.interval, limit=args.limit,
                                 on_invalid="skip")
    skipped = client.last_skipped_rows
    _, interval_seconds = INTERVALS[args.interval]
    now = int(time.time())
    closed = tuple(
        StrategyCandle(c.timestamp, c.open, c.high, c.low, c.close, c.volume)
        for c in fetched if c.timestamp + interval_seconds <= now
    )  # revalidation through the strategy Candle type (fail-closed conversion)
    dropped = len(fetched) - len(closed)

    config = BacktestConfig(fee_rate=Decimal(args.fee), slippage_rate=Decimal(args.slippage))
    # 0.95 notional cap leaves entry-fee headroom (see app.strategy.policy).
    policy = TrendPolicy(timeframe=args.interval, market_type=args.market,
                         symbol=args.symbol,
                         limits=RiskLimits(max_notional_fraction=Decimal("0.95")))
    report = evaluate_walk_forward_steps(
        closed, factory=make_step_factory(policy, config),
        train_size=args.train, test_size=args.test, config=config)

    last_test = closed[-args.test:]
    paper = run_paper_session(
        last_test, policy=policy,
        engine=None,
    )
    output = {
        "data": {"symbol": args.symbol, "interval": args.interval,
                 "fetched_valid": len(fetched), "invalid_skipped": skipped,
                 "forming_dropped": dropped,
                 "used": len(closed),
                 "first_ts": closed[0].timestamp, "last_ts": closed[-1].timestamp},
        "config": {"train": args.train, "test": args.test, "fee": args.fee,
                   "slippage": args.slippage, "market": args.market,
                   "policy_notional_cap": "0.95"},
        "walk_forward": report_to_json(report),
        "paper_session_last_test_window": {
            "candles": paper.candles_processed,
            "trades": paper.trade_count,
            "total_fees": str(paper.total_fees),
            "initial_equity": str(paper.initial_equity),
            "final_equity": str(paper.final_equity),
            "net_pnl": str(paper.net_pnl),
        },
        "disclaimer": ("Offline simulation on public historical data; no orders, "
                       "no credentials, no live-trading capability anywhere in the repo."),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

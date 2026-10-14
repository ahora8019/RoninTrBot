#!/usr/bin/env python3
"""Multi-parameter / multi-symbol OOS sensitivity study on real LBank data.

Grid evaluation over SYMBOLS x INTERVALS x THRESHOLDS on public, read-only
market data: for each (symbol, interval) candles are fetched ONCE (with a
local JSON cache so reruns don't hammer the API), the forming candle is
dropped, and the risk-sized trend policy is walk-forward evaluated per
threshold with identical train/test geometry and cost assumptions.

No credentials, no orders, no paid services. Everything here is orchestration
over unit-tested components; the live part is the read-only kline fetch.
Results print as JSON (machine-readable) with a ranked summary.
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
from app.risk.engine import RiskLimits  # noqa: E402
from app.strategy.indicators import Candle as StrategyCandle  # noqa: E402
from app.strategy.policy import TrendPolicy, trend_intent  # noqa: E402
from app.validation.backtest import BacktestConfig  # noqa: E402
from app.validation.walkforward import evaluate_walk_forward_steps  # noqa: E402

DEFAULT_SYMBOLS = ("btc_usdt", "eth_usdt")
DEFAULT_INTERVALS = ("1h", "4h")
DEFAULT_THRESHOLDS = (60, 70, 80)


def load_or_fetch(client: LBankMarketDataClient, symbol: str, interval: str,
                  limit: int, cache_dir: Path, max_cache_age_s: int = 3600) -> tuple:
    """Return validated strategy candles; JSON cache is a research convenience."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{symbol}_{interval}_{limit}.json"
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < max_cache_age_s:
        raw = json.loads(cache_path.read_text())
    else:
        fetched = client.fetch_ohlcv(symbol, interval, limit=limit, on_invalid="skip")
        raw = {
            "skipped_invalid": client.last_skipped_rows,
            "candles": [[c.timestamp, str(c.open), str(c.high), str(c.low),
                         str(c.close), str(c.volume)] for c in fetched],
        }
        cache_path.write_text(json.dumps(raw))
    _, interval_seconds = INTERVALS[interval]
    now = int(time.time())
    candles = tuple(
        StrategyCandle(int(r[0]), Decimal(r[1]), Decimal(r[2]), Decimal(r[3]),
                       Decimal(r[4]), Decimal(r[5]))  # revalidated here, fail-closed
        for r in raw["candles"] if int(r[0]) + interval_seconds <= now
    )
    return candles, raw["skipped_invalid"]


def evaluate_combo(candles: tuple, *, symbol: str, interval: str, threshold: int,
                   train: int, test: int, fee: Decimal, slippage: Decimal) -> dict:
    config = BacktestConfig(fee_rate=fee, slippage_rate=slippage)
    policy = TrendPolicy(timeframe=interval, symbol=symbol,
                         limits=RiskLimits(max_notional_fraction=Decimal("0.95")),
                         threshold=threshold)

    def factory(history, timestamp):
        return trend_intent(history, equity=config.initial_equity, policy=policy)

    report = evaluate_walk_forward_steps(
        candles, factory=factory, train_size=train, test_size=test, config=config)
    return {
        "symbol": symbol, "interval": interval, "threshold": threshold,
        "windows": len(report.windows), "trades": report.total_trades,
        "wins": report.win_count, "losses": report.loss_count,
        "initial_equity": str(config.initial_equity),
        "final_equity": str(report.final_equity),
        "net_pnl": str(report.net_pnl),
        "total_fees": str(report.total_fees),
        "max_drawdown_fraction": str(report.max_drawdown_fraction),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--intervals", default=",".join(DEFAULT_INTERVALS))
    parser.add_argument("--thresholds", default=",".join(str(t) for t in DEFAULT_THRESHOLDS))
    parser.add_argument("--limit", type=int, default=1200)
    parser.add_argument("--train", type=int, default=400)
    parser.add_argument("--test", type=int, default=100)
    parser.add_argument("--fee", default="0.001")
    parser.add_argument("--slippage", default="0.0005")
    parser.add_argument("--cache-dir", default="/tmp/lbank_research_cache")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    symbols = [s.strip().lower() for s in args.symbols.split(",") if s.strip()]
    intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]
    thresholds = [int(s) for s in args.thresholds.split(",") if s.strip()]
    fee, slippage = Decimal(args.fee), Decimal(args.slippage)

    client = LBankMarketDataClient()
    data_info, results, errors = {}, [], []
    for symbol in symbols:
        for interval in intervals:
            key = f"{symbol}/{interval}"
            try:
                candles, skipped = load_or_fetch(
                    client, symbol, interval, args.limit, Path(args.cache_dir))
            except Exception as exc:  # recorded, not fatal to other combos
                errors.append({"combo": key, "error": f"{type(exc).__name__}: {exc}"})
                continue
            data_info[key] = {"candles": len(candles), "invalid_skipped": skipped,
                              "first_ts": candles[0].timestamp,
                              "last_ts": candles[-1].timestamp}
            for threshold in thresholds:
                try:
                    results.append(evaluate_combo(
                        candles, symbol=symbol, interval=interval,
                        threshold=threshold, train=args.train, test=args.test,
                        fee=fee, slippage=slippage))
                except ValueError as exc:
                    errors.append({"combo": f"{key}/t{threshold}", "error": str(exc)})

    ranked = sorted(results, key=lambda r: Decimal(r["net_pnl"]), reverse=True)
    output = {
        "asof_unix": int(time.time()),
        "config": {"train": args.train, "test": args.test, "limit": args.limit,
                   "fee": args.fee, "slippage": args.slippage,
                   "policy_notional_cap": "0.95", "markets": "futures"},
        "data": data_info,
        "results_ranked_by_net_pnl": ranked,
        "errors": errors,
        "disclaimer": ("Offline simulation on public historical data; no orders, "
                       "no credentials, no live-trading capability anywhere in the repo. "
                       "Rankings are descriptive evidence across OOS windows, not advice."),
    }
    text = json.dumps(output, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

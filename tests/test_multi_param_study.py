"""Offline tests for the grid-evaluation core of scripts/multi_param_study.py.

The CLI's network part is exercised live only in the study report; the pure
evaluation entry point is pinned here against synthetic series (no network).
"""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from multi_param_study import evaluate_combo, load_or_fetch  # noqa: E402
from app.strategy.indicators import Candle  # noqa: E402


def rising(n=700, start=100.0, step=0.4):
    out = []
    for i in range(n):
        close = Decimal(str(round(start + step * i, 6)))
        out.append(Candle(i, close - Decimal("0.02"), close + Decimal("0.05"),
                          close - Decimal("0.05"), close, Decimal("5")))
    return tuple(out)


def test_evaluate_combo_returns_expected_metrics_shape_and_is_deterministic():
    candles = rising()
    kwargs = dict(symbol="test_usdt", interval="1h", threshold=70,
                  train=200, test=100, fee=Decimal("0.001"), slippage=Decimal("0.0005"))
    r1 = evaluate_combo(candles, **kwargs)
    r2 = evaluate_combo(candles, **kwargs)
    assert r1 == r2
    assert set(r1) == {"symbol", "interval", "strategy", "threshold", "windows",
                       "trades", "wins", "losses", "initial_equity", "final_equity",
                       "net_pnl", "total_fees", "max_drawdown_fraction"}
    assert r1["strategy"] == "trend"
    assert r1["windows"] == 5
    assert r1["trades"] == r1["wins"] + r1["losses"] or r1["trades"] >= r1["wins"] + r1["losses"]
    assert Decimal(r1["net_pnl"]) == Decimal(r1["final_equity"]) - Decimal(r1["initial_equity"])


def test_evaluate_combo_requires_full_window():
    with pytest.raises(ValueError, match="no full walk-forward window"):
        evaluate_combo(rising(250), symbol="test_usdt", interval="1h", threshold=70,
                       train=200, test=100, fee=Decimal("0"), slippage=Decimal("0"))


def test_threshold_raises_reduce_signal_counts_on_mixed_series():
    # Alternating drift: scores vary; a higher threshold must not increase trades.
    n, candles = 700, []
    for i in range(n):
        drift = 0.5 if (i // 60) % 2 == 0 else -0.5
        close = Decimal(str(round(150 + drift * (i % 60), 6)))
        candles.append(Candle(i, close - Decimal("0.02"), close + Decimal("0.05"),
                              close - Decimal("0.05"), close, Decimal("5")))
    kwargs = dict(symbol="test_usdt", interval="1h", train=200, test=100,
                  fee=Decimal("0"), slippage=Decimal("0"))
    low = evaluate_combo(tuple(candles), threshold=60, **kwargs)
    high = evaluate_combo(tuple(candles), threshold=90, **kwargs)
    assert high["trades"] <= low["trades"]


def test_load_or_fetch_uses_cache_without_transport(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "btc_usdt_1h_10.json").write_text(
        '[["__ignored__"]]' if False else '{"skipped_invalid": 3, "candles": []}')
    # No transport allowed: proving the cache path never touches the network.
    def forbidden_transport(url, params, timeout):
        raise AssertionError("must not fetch when a fresh cache exists")

    from app.market_data.lbank import LBankMarketDataClient
    client = LBankMarketDataClient(transport=forbidden_transport)
    candles, skipped = load_or_fetch(client, "btc_usdt", "1h", 10, cache_dir)
    assert candles == () and skipped == 3

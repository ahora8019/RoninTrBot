"""Parity tests: the worker's JS analysis port must match the Python core.

Discrete outputs (direction, score, eligible, regime) must be EXACTLY equal;
numeric internals may differ within float64-vs-Decimal tolerance.
Skips cleanly when Node.js is unavailable; CI runners ship Node.
"""
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.strategy.indicators import Candle, analyze_timeframe
from app.strategy.registry import classify_regime

WORKER_LIB = Path(__file__).resolve().parent.parent / "workers" / "ronin-panel" / "analysis.js"
NODE = shutil.which("node")
TOL = 1e-9


def make_candles(spec: str, n: int = 60):
    out = []
    price = 100.0
    for i in range(n):
        if spec == "flat":
            move = 0.0
        elif spec == "up":
            move = 0.4 + 0.05 * ((i % 5) - 2)
        elif spec == "down":
            move = -0.4 - 0.05 * ((i % 5) - 2)
        elif spec == "zigzag":
            move = 1.2 if i % 2 == 0 else -1.2
        else:
            raise ValueError(spec)
        price = price + move
        out.append((i, price - 0.02, price + 0.35, price - 0.35, price, 5 + (i % 3)))
    return out


def python_expected(spec: str, timeframe: str = "1h") -> dict:
    candles = tuple(Candle(*row) for row in make_candles(spec))
    a = analyze_timeframe(timeframe, candles)
    return {
        "score": a.score,
        "direction": a.direction,
        "eligible": a.score >= 70,
        "regime": classify_regime(candles),
        "sma_fast": float(a.sma_fast),
        "sma_slow": float(a.sma_slow),
        "rsi": float(a.rsi),
        "atr": float(a.atr),
    }


def js_actual(spec: str, timeframe: str = "1h") -> dict:
    payload = json.dumps({
        "timeframe": timeframe,
        "candles": [{"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}
                    for ts, o, h, l, c, v in make_candles(spec)],
    })
    result = subprocess.run(
        [NODE, str(WORKER_LIB), "--selftest"], input=payload, capture_output=True,
        text=True, timeout=30)
    assert result.returncode == 0, f"node failed: {result.stderr[:300]}"
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the parity harness")
@pytest.mark.parametrize("spec", ["flat", "up", "down", "zigzag"])
def test_worker_analysis_matches_python_core(spec):
    expected = python_expected(spec)
    actual = js_actual(spec)
    # Discrete outputs: exact.
    assert actual["direction"] == expected["direction"]
    assert actual["score"] == expected["score"]
    assert actual["eligible"] == expected["eligible"]
    assert actual["regime"] == expected["regime"]
    # Numeric internals: float64 vs Decimal tolerance.
    for field in ("sma_fast", "sma_slow", "rsi", "atr"):
        assert abs(actual[field] - expected[field]) < TOL, f"{field} drift"


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the parity harness")
def test_worker_port_rejects_structural_defects_like_python():
    candles = [{"timestamp": i, "open": 100, "high": 101, "low": 99, "close": 100,
                "volume": 1} for i in range(60)]
    candles[30]["low"] = 103  # OHLC inconsistent
    result = subprocess.run([NODE, str(WORKER_LIB), "--selftest"],
                            input=json.dumps({"candles": candles}), capture_output=True,
                            text=True, timeout=30)
    assert result.returncode != 0, "JS port must fail closed on invalid OHLC"

@pytest.mark.skipif(NODE is None, reason="Node.js is required for the parity harness")
@pytest.mark.parametrize(("field", "bad_value"), [
    ("open", None),
    ("high", ""),
    ("low", True),
    ("close", False),
    ("volume", "   "),
])
def test_worker_port_rejects_values_javascript_would_coerce(field, bad_value):
    """Do not let null/blank/boolean candle values silently become 0 or 1."""
    candles = [{"timestamp": i, "open": 100, "high": 101, "low": 99,
                "close": 100, "volume": 1} for i in range(60)]
    candles[30][field] = bad_value
    result = subprocess.run([NODE, str(WORKER_LIB), "--selftest"],
                            input=json.dumps({"candles": candles}), capture_output=True,
                            text=True, timeout=30)
    assert result.returncode != 0, f"JS port must reject invalid {field}={bad_value!r}"

import pytest

from app.core.market_scanner import (
    MarketSnapshot,
    MarketType,
    ScannerPolicy,
    SymbolStatus,
    classify_snapshot,
    randomized_symbol_order,
    scan_snapshots,
)


def snapshot(symbol="BTCUSDT", **overrides):
    values = dict(
        symbol=symbol,
        market_type=MarketType.SPOT,
        price=100.0,
        volume_quote=500_000.0,
        spread_bps=5.0,
        volatility_pct=2.0,
        data_age_seconds=10.0,
        trend="up",
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def test_healthy_market_is_suitable():
    assert classify_snapshot(snapshot()).status is SymbolStatus.SUITABLE


def test_stale_or_illiquid_market_is_rejected():
    result = classify_snapshot(snapshot(data_age_seconds=300, volume_quote=10))
    assert result.status is SymbolStatus.UNSUITABLE
    assert "stale_data" in result.reasons
    assert "low_liquidity" in result.reasons


def test_risky_volatility_is_marked_not_automatically_traded():
    result = classify_snapshot(snapshot(volatility_pct=6.0))
    assert result.status is SymbolStatus.RISKY


def test_unconfirmed_trend_is_no_trade():
    assert classify_snapshot(snapshot(trend="unknown")).status is SymbolStatus.NO_TRADE


def test_invalid_price_fails_closed():
    result = classify_snapshot(snapshot(price=0))
    assert result.status is SymbolStatus.UNSUITABLE
    assert "invalid_price" in result.reasons


def test_randomized_order_keeps_each_symbol_once():
    result = randomized_symbol_order(["btcusdt", "ETHUSDT", "BTCUSDT", " SOLUSDT "], seed=7)
    assert len(result) == 3
    assert set(result) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


def test_scan_continues_after_unsuitable_symbol_and_keeps_market_types_separate():
    snapshots = [
        snapshot("BADUSDT", volume_quote=1),
        snapshot("ETHUSDT", market_type=MarketType.FUTURES),
    ]
    results = scan_snapshots(snapshots)
    assert len(results) == 2
    assert results[0].status is SymbolStatus.UNSUITABLE
    assert results[1].status is SymbolStatus.SUITABLE
    assert results[1].market_type is MarketType.FUTURES


def test_custom_policy_is_respected():
    result = classify_snapshot(snapshot(volume_quote=150), ScannerPolicy(min_quote_volume=100))
    assert result.status is SymbolStatus.SUITABLE

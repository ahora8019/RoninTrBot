"""Safe multi-symbol scanner primitives for paper-mode strategy orchestration.

This module does not fetch market data or place orders. Callers provide normalized
snapshots and must keep Spot and Futures risk/execution policies separate.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import random
from typing import Iterable, Sequence


class MarketType(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"


class SymbolStatus(str, Enum):
    SUITABLE = "SUITABLE"
    RISKY = "RISKY"
    UNSUITABLE = "UNSUITABLE"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    market_type: MarketType
    price: float
    volume_quote: float
    spread_bps: float
    volatility_pct: float
    data_age_seconds: float
    trend: str = "unknown"


@dataclass(frozen=True)
class ScannerPolicy:
    min_quote_volume: float = 100_000.0
    max_spread_bps: float = 20.0
    max_volatility_pct: float = 8.0
    max_data_age_seconds: float = 120.0
    risky_volatility_pct: float = 5.0


@dataclass(frozen=True)
class SymbolDecision:
    symbol: str
    market_type: MarketType
    status: SymbolStatus
    reasons: tuple[str, ...]


def classify_snapshot(
    snapshot: MarketSnapshot,
    policy: ScannerPolicy = ScannerPolicy(),
) -> SymbolDecision:
    """Classify one normalized snapshot; invalid/stale data fails closed."""
    reasons: list[str] = []
    numeric_values = (
        snapshot.price,
        snapshot.volume_quote,
        snapshot.spread_bps,
        snapshot.volatility_pct,
        snapshot.data_age_seconds,
    )
    if not snapshot.symbol.strip():
        reasons.append("missing_symbol")
    if any(value != value or value in (float("inf"), float("-inf")) for value in numeric_values):
        reasons.append("non_finite_market_data")
    if snapshot.price <= 0:
        reasons.append("invalid_price")
    if snapshot.volume_quote < 0 or snapshot.spread_bps < 0 or snapshot.volatility_pct < 0 or snapshot.data_age_seconds < 0:
        reasons.append("negative_market_metric")
    if reasons:
        return SymbolDecision(snapshot.symbol, snapshot.market_type, SymbolStatus.UNSUITABLE, tuple(reasons))

    if snapshot.data_age_seconds > policy.max_data_age_seconds:
        reasons.append("stale_data")
    if snapshot.volume_quote < policy.min_quote_volume:
        reasons.append("low_liquidity")
    if snapshot.spread_bps > policy.max_spread_bps:
        reasons.append("wide_spread")
    if snapshot.volatility_pct > policy.max_volatility_pct:
        reasons.append("extreme_volatility")
    if reasons:
        return SymbolDecision(snapshot.symbol, snapshot.market_type, SymbolStatus.UNSUITABLE, tuple(reasons))

    if snapshot.volatility_pct >= policy.risky_volatility_pct:
        return SymbolDecision(snapshot.symbol, snapshot.market_type, SymbolStatus.RISKY, ("elevated_volatility_requires_stricter_risk_gate",))

    if snapshot.trend not in {"up", "down", "sideways"}:
        return SymbolDecision(snapshot.symbol, snapshot.market_type, SymbolStatus.NO_TRADE, ("trend_not_confirmed",))
    return SymbolDecision(snapshot.symbol, snapshot.market_type, SymbolStatus.SUITABLE, ("market_filters_passed",))


def randomized_symbol_order(symbols: Iterable[str], *, seed: int | None = None) -> list[str]:
    """Return a shuffled, de-duplicated list so each symbol is considered once per pass."""
    unique = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    random.Random(seed).shuffle(unique)
    return unique


def scan_snapshots(
    snapshots: Sequence[MarketSnapshot],
    policy: ScannerPolicy = ScannerPolicy(),
) -> list[SymbolDecision]:
    """Classify every supplied snapshot, continuing after unsuitable symbols."""
    return [classify_snapshot(snapshot, policy) for snapshot in snapshots]

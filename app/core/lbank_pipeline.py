"""Paper-only LBank Spot market scan orchestration.

Public market data only. No account endpoints, credentials, or order placement.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import time
from typing import Callable, Sequence

from app.core.lbank_public import LBankAPIError, LBankSpotPublicClient, LBankTicker24h, _number, _successful, _normalize_symbol
from app.core.market_scanner import (
    MarketSnapshot, ScannerPolicy, SymbolDecision, SymbolStatus,
    classify_snapshot, randomized_symbol_order,
)
from app.core.strategy_engine import Candle, SignalAction, StrategyPolicy, StrategySignal, evaluate_closed_candles


INTERVAL_SECONDS = {
    "minute1": 60, "minute5": 300, "minute15": 900, "minute30": 1800,
    "hour1": 3600, "hour4": 14400, "hour8": 28800, "hour12": 43200,
    "day1": 86400, "week1": 604800, "month1": 2592000,
}


@dataclass(frozen=True, slots=True)
class SpotScanResult:
    symbol: str
    decision: SymbolDecision
    signal: StrategySignal
    candle_count: int


def fetch_closed_candles(
    client: LBankSpotPublicClient,
    symbol: str,
    *,
    interval: str = "minute15",
    limit: int = 100,
    now: float | None = None,
) -> list[Candle]:
    """Fetch and validate K-lines, excluding the still-forming candle."""
    if interval not in INTERVAL_SECONDS:
        raise ValueError("unsupported_candle_interval")
    if not 1 <= limit <= 2000:
        raise ValueError("limit_out_of_range")
    current = time.time() if now is None else now
    if not math.isfinite(current):
        raise ValueError("now_must_be_finite")
    data = _successful(client._get("/v2/kline.do", {
        "symbol": _normalize_symbol(symbol),
        "size": str(limit),
        "type": interval,
        "time": str(max(0, int(current) - INTERVAL_SECONDS[interval] * (limit + 2))),
    }))
    if not isinstance(data, list):
        raise LBankAPIError("invalid_kline_list")
    seconds = INTERVAL_SECONDS[interval]
    candles: list[Candle] = []
    for row in data:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise LBankAPIError("invalid_kline_row")
        timestamp = int(_number(row[0], "kline_timestamp"))
        values = [_number(row[i], f"kline_{i}") for i in range(1, 6)]
        candle = Candle(
            timestamp=timestamp,
            open=values[0],
            high=values[1],
            low=values[2],
            close=values[3],
            volume=values[4],
            is_closed=(timestamp + seconds <= current),
        )
        if min(candle.open, candle.high, candle.low, candle.close) <= 0 or candle.volume < 0:
            raise LBankAPIError("invalid_kline_values")
        if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(candle.open, candle.close, candle.high):
            raise LBankAPIError("invalid_kline_ohlc")
        candles.append(candle)
    candles.sort(key=lambda item: item.timestamp)
    if any(candles[i].timestamp >= candles[i + 1].timestamp for i in range(len(candles) - 1)):
        raise LBankAPIError("duplicate_or_nonchronological_klines")
    return [candle for candle in candles if candle.is_closed]


def scan_lbank_spot(
    client: LBankSpotPublicClient,
    *,
    max_symbols: int = 20,
    interval: str = "minute15",
    candle_limit: int = 100,
    scanner_policy: ScannerPolicy = ScannerPolicy(),
    strategy_policy: StrategyPolicy = StrategyPolicy(),
    seed: int | None = None,
    now: Callable[[], float] = time.time,
) -> list[SpotScanResult]:
    """One bounded pass: filter liquid markets, inspect candles, and return paper signals."""
    if max_symbols < 1:
        raise ValueError("max_symbols_must_be_positive")
    tickers = client.get_24h_tickers()
    eligible = [ticker for ticker in tickers if ticker.symbol.endswith("_usdt")]
    ordered_symbols = randomized_symbol_order((ticker.symbol for ticker in eligible), seed=seed)
    ticker_by_symbol = {ticker.symbol: ticker for ticker in eligible}
    results: list[SpotScanResult] = []
    for symbol in ordered_symbols:
        if len(results) >= max_symbols:
            break
        ticker = ticker_by_symbol[symbol]
        try:
            book = client.get_book_ticker(symbol)
            snapshot = client.to_snapshot(ticker, book)
            decision = classify_snapshot(snapshot, scanner_policy)
            if decision.status is not SymbolStatus.SUITABLE:
                results.append(SpotScanResult(snapshot.symbol, decision, _no_trade_from_decision(decision), 0))
                continue
            candles = fetch_closed_candles(
                client, symbol, interval=interval, limit=candle_limit, now=now()
            )
            signal = evaluate_closed_candles(candles, strategy_policy)
            results.append(SpotScanResult(snapshot.symbol, decision, signal, len(candles)))
        except (LBankAPIError, ValueError, TimeoutError, OSError) as exc:
            failed = SymbolDecision(snapshot.symbol if "snapshot" in locals() else symbol.replace("_", "").upper(),
                                    snapshot.market_type if "snapshot" in locals() else __import__("app.core.market_scanner", fromlist=["MarketType"]).MarketType.SPOT,
                                    SymbolStatus.NO_TRADE, (f"market_data_unavailable:{type(exc).__name__}",))
            results.append(SpotScanResult(failed.symbol, failed, _no_trade_from_decision(failed), 0))
    return results


def _no_trade_from_decision(decision: SymbolDecision) -> StrategySignal:
    from app.core.strategy_engine import StrategyName
    return StrategySignal(
        action=SignalAction.NO_TRADE, strategy=StrategyName.NONE,
        entry=None, stop_loss=None, take_profit=None, score=0,
        reasons=("scanner_gate_blocked", *decision.reasons),
    )

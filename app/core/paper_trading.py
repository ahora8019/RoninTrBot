"""Deterministic, in-memory paper broker for long-only Spot simulation.

No exchange account access, credentials, persistence, or real order placement.
A caller supplies closed candles in chronological order. When SL and TP both
fall inside one candle, stop-loss is assumed first (conservative fill policy).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from app.core.market_scanner import MarketType
from app.core.risk_engine import RiskDecisionStatus, RiskPolicy, assess_paper_position
from app.core.strategy_engine import Candle, SignalAction, StrategySignal


class PaperTradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED_TAKE_PROFIT = "CLOSED_TAKE_PROFIT"
    CLOSED_STOP_LOSS = "CLOSED_STOP_LOSS"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class PaperTradingPolicy:
    starting_balance: float = 1000.0
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005
    risk_policy: RiskPolicy = RiskPolicy()
    market_type: MarketType = MarketType.SPOT


@dataclass(frozen=True, slots=True)
class PaperTrade:
    trade_id: int
    symbol: str
    status: PaperTradeStatus
    entry_time: int | None
    exit_time: int | None
    entry_price: float | None
    exit_price: float | None
    quantity: float
    notional: float
    stop_loss: float | None
    take_profit: float | None
    entry_fee: float
    exit_fee: float
    gross_pnl: float
    net_pnl: float
    balance_after: float
    reason: str


class PaperBroker:
    """Small testable paper broker; balance changes only on simulated fills."""

    def __init__(self, policy: PaperTradingPolicy = PaperTradingPolicy()) -> None:
        values = (policy.starting_balance, policy.fee_rate, policy.slippage_rate)
        if any(not math.isfinite(v) for v in values):
            raise ValueError("paper_policy_values_must_be_finite")
        if policy.starting_balance < 0:
            raise ValueError("starting_balance_must_not_be_negative")
        if not 0 <= policy.fee_rate < 0.05:
            raise ValueError("fee_rate_out_of_range")
        if not 0 <= policy.slippage_rate < 0.05:
            raise ValueError("slippage_rate_out_of_range")
        self.policy = policy
        self.balance = policy.starting_balance
        self._next_id = 1
        self._open: PaperTrade | None = None
        self.trades: list[PaperTrade] = []

    @property
    def open_trade(self) -> PaperTrade | None:
        return self._open

    def on_signal(self, symbol: str, signal: StrategySignal, candle: Candle) -> PaperTrade | None:
        """Open only from a PAPER_LONG signal on a closed candle; never pyramids."""
        if not candle.is_closed:
            return None
        if self._open is not None or signal.action is not SignalAction.PAPER_LONG:
            return None
        if not symbol or signal.entry is None or signal.stop_loss is None or signal.take_profit is None:
            return None
        if any(not math.isfinite(v) for v in (signal.entry, signal.stop_loss, signal.take_profit)):
            return None
        fill_price = signal.entry * (1.0 + self.policy.slippage_rate)
        effective_fee = self.policy.fee_rate + self.policy.slippage_rate
        assessment = assess_paper_position(
            balance=self.balance,
            entry_price=fill_price,
            stop_price=signal.stop_loss,
            take_profit_price=signal.take_profit,
            market_type=self.policy.market_type,
            policy=RiskPolicy(
                risk_fraction=self.policy.risk_policy.risk_fraction,
                max_position_fraction=self.policy.risk_policy.max_position_fraction,
                max_leverage=self.policy.risk_policy.max_leverage,
                estimated_cost_fraction=effective_fee,
            ),
        )
        if assessment.status is not RiskDecisionStatus.APPROVED_FOR_PAPER or assessment.quantity <= 0:
            return self._record_rejection(symbol, candle.timestamp, signal, "risk_gate_rejected")
        entry_fee = assessment.notional * self.policy.fee_rate
        trade = PaperTrade(
            trade_id=self._next_id, symbol=symbol.upper(), status=PaperTradeStatus.OPEN,
            entry_time=candle.timestamp, exit_time=None, entry_price=fill_price, exit_price=None,
            quantity=assessment.quantity, notional=assessment.notional,
            stop_loss=signal.stop_loss, take_profit=signal.take_profit,
            entry_fee=entry_fee, exit_fee=0.0, gross_pnl=0.0, net_pnl=-entry_fee,
            balance_after=self.balance, reason="paper_entry",
        )
        self._next_id += 1
        self._open = trade
        self.trades.append(trade)
        return trade

    def on_candle(self, candle: Candle) -> PaperTrade | None:
        """Check stop/target on a later closed candle and book conservative fills."""
        trade = self._open
        if trade is None or not candle.is_closed or trade.entry_time is None or candle.timestamp <= trade.entry_time:
            return None
        stop = trade.stop_loss
        target = trade.take_profit
        if stop is None or target is None:
            return None
        hit_stop = candle.low <= stop
        hit_target = candle.high >= target
        if not hit_stop and not hit_target:
            return None
        if hit_stop:  # Conservative if both levels are touched in the same candle.
            raw_exit = stop
            status = PaperTradeStatus.CLOSED_STOP_LOSS
            reason = "stop_loss_first_or_only"
        else:
            raw_exit = target
            status = PaperTradeStatus.CLOSED_TAKE_PROFIT
            reason = "take_profit_hit"
        exit_price = raw_exit * (1.0 - self.policy.slippage_rate)
        gross_pnl = (exit_price - trade.entry_price) * trade.quantity
        exit_fee = exit_price * trade.quantity * self.policy.fee_rate
        net_pnl = gross_pnl - trade.entry_fee - exit_fee
        self.balance += gross_pnl - trade.entry_fee - exit_fee
        closed = PaperTrade(
            trade_id=trade.trade_id, symbol=trade.symbol, status=status,
            entry_time=trade.entry_time, exit_time=candle.timestamp,
            entry_price=trade.entry_price, exit_price=exit_price,
            quantity=trade.quantity, notional=trade.notional,
            stop_loss=trade.stop_loss, take_profit=trade.take_profit,
            entry_fee=trade.entry_fee, exit_fee=exit_fee,
            gross_pnl=gross_pnl, net_pnl=net_pnl, balance_after=self.balance,
            reason=reason,
        )
        self._replace_trade(closed)
        self._open = None
        return closed

    def summary(self) -> dict[str, float | int]:
        closed = [t for t in self.trades if t.status in {
            PaperTradeStatus.CLOSED_STOP_LOSS, PaperTradeStatus.CLOSED_TAKE_PROFIT
        }]
        wins = [t for t in closed if t.net_pnl > 0]
        losses = [t for t in closed if t.net_pnl < 0]
        gross_profit = sum(t.net_pnl for t in wins)
        gross_loss = abs(sum(t.net_pnl for t in losses))
        return {
            "balance": round(self.balance, 8),
            "trades_opened": sum(t.status is PaperTradeStatus.OPEN for t in self.trades) + len(closed),
            "trades_closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "net_pnl": round(sum(t.net_pnl for t in closed), 8),
            "profit_factor": round(gross_profit / gross_loss, 8) if gross_loss else (float("inf") if gross_profit else 0.0),
        }

    def _record_rejection(self, symbol: str, timestamp: int, signal: StrategySignal, reason: str) -> PaperTrade:
        trade = PaperTrade(
            trade_id=self._next_id, symbol=symbol.upper(), status=PaperTradeStatus.REJECTED,
            entry_time=timestamp, exit_time=None, entry_price=signal.entry, exit_price=None,
            quantity=0.0, notional=0.0, stop_loss=signal.stop_loss, take_profit=signal.take_profit,
            entry_fee=0.0, exit_fee=0.0, gross_pnl=0.0, net_pnl=0.0,
            balance_after=self.balance, reason=reason,
        )
        self._next_id += 1
        self.trades.append(trade)
        return trade

    def _replace_trade(self, updated: PaperTrade) -> None:
        for index, existing in enumerate(self.trades):
            if existing.trade_id == updated.trade_id:
                self.trades[index] = updated
                return

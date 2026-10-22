import pytest

from app.core.paper_store import SQLitePaperStore
from app.core.paper_trading import PaperBroker, PaperTradingPolicy, PaperTradeStatus
from app.core.strategy_engine import Candle, SignalAction, StrategyName, StrategySignal


def signal():
    return StrategySignal(
        action=SignalAction.PAPER_LONG,
        strategy=StrategyName.RSI_REVERSAL,
        entry=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        score=72,
        reasons=("paper_only",),
    )


def candle(ts, *, high=101, low=99, closed=True):
    return Candle(ts, 100, high, low, 100, 100, closed)


def test_round_trip_restores_open_trade_and_next_id(tmp_path):
    path = tmp_path / "paper.sqlite3"
    policy = PaperTradingPolicy(starting_balance=1000, fee_rate=0, slippage_rate=0)
    original = PaperBroker(policy)
    opened = original.on_signal("BTCUSDT", signal(), candle(1))
    assert opened is not None and opened.status is PaperTradeStatus.OPEN

    store = SQLitePaperStore(path)
    store.save(original)

    restored = PaperBroker(policy)
    assert store.restore(restored) is True
    assert restored.balance == original.balance
    assert restored.open_trade == original.open_trade
    assert restored.trades == original.trades

    closed = restored.on_candle(candle(2, high=111, low=99))
    assert closed is not None and closed.status is PaperTradeStatus.CLOSED_TAKE_PROFIT
    store.save(restored)

    after_restart = PaperBroker(policy)
    assert store.restore(after_restart) is True
    assert after_restart.open_trade is None
    assert after_restart.trades == restored.trades
    assert after_restart.balance == restored.balance

    next_trade = after_restart.on_signal("ETHUSDT", signal(), candle(3))
    assert next_trade is not None
    assert next_trade.trade_id == 2


def test_restore_returns_false_when_store_is_empty(tmp_path):
    store = SQLitePaperStore(tmp_path / "empty.sqlite3")
    assert store.restore(PaperBroker()) is False


def test_restore_refuses_to_overwrite_active_broker(tmp_path):
    store = SQLitePaperStore(tmp_path / "paper.sqlite3")
    broker = PaperBroker()
    broker.on_signal("BTCUSDT", signal(), candle(1))
    with pytest.raises(ValueError, match="restore_requires_fresh_broker"):
        store.restore(broker)


def test_save_and_restore_preserve_rejected_trade_history(tmp_path):
    store = SQLitePaperStore(tmp_path / "paper.sqlite3")
    broker = PaperBroker(PaperTradingPolicy(starting_balance=0))
    rejected = broker.on_signal("BTCUSDT", signal(), candle(1))
    assert rejected is not None and rejected.status is PaperTradeStatus.REJECTED
    store.save(broker)

    restored = PaperBroker(PaperTradingPolicy(starting_balance=0))
    assert store.restore(restored)
    assert restored.trades == broker.trades
    assert restored.open_trade is None


def test_broker_persists_each_fill_when_store_is_configured(tmp_path):
    policy = PaperTradingPolicy(starting_balance=1000, fee_rate=0, slippage_rate=0)
    store = SQLitePaperStore(tmp_path / "integrated.sqlite3")
    broker = PaperBroker(policy, store=store)
    opened = broker.on_signal("BTCUSDT", signal(), candle(1))
    assert opened is not None

    restored = PaperBroker(policy, store=store, restore=True)
    assert restored.open_trade == opened
    closed = restored.on_candle(candle(2, high=111, low=99))
    assert closed is not None and closed.status is PaperTradeStatus.CLOSED_TAKE_PROFIT

    after_restart = PaperBroker(policy, store=store, restore=True)
    assert after_restart.open_trade is None
    assert after_restart.trades[-1] == closed
    assert after_restart.balance == closed.balance_after

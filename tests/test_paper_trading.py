from app.core.paper_trading import PaperBroker, PaperTradingPolicy, PaperTradeStatus
from app.core.strategy_engine import Candle, SignalAction, StrategyName, StrategySignal


def signal():
    return StrategySignal(
        action=SignalAction.PAPER_LONG, strategy=StrategyName.RSI_REVERSAL,
        entry=100.0, stop_loss=95.0, take_profit=110.0, score=72,
        reasons=("paper_only",),
    )


def candle(ts, open_=100, high=101, low=99, close=100, closed=True):
    return Candle(ts, open_, high, low, close, 100, closed)


def test_opens_paper_trade_only_on_closed_candle_and_caps_position():
    broker = PaperBroker(PaperTradingPolicy(starting_balance=1000))
    assert broker.on_signal("BTCUSDT", signal(), candle(1, closed=False)) is None
    trade = broker.on_signal("BTCUSDT", signal(), candle(1))
    assert trade.status is PaperTradeStatus.OPEN
    assert trade.quantity > 0
    assert trade.notional <= 200
    assert broker.open_trade is trade


def test_no_pyramiding_while_trade_open():
    broker = PaperBroker()
    broker.on_signal("BTCUSDT", signal(), candle(1))
    assert broker.on_signal("ETHUSDT", signal(), candle(2)) is None
    assert len(broker.trades) == 1


def test_stop_loss_closes_trade_and_books_fees():
    broker = PaperBroker(PaperTradingPolicy(starting_balance=1000, fee_rate=0.001, slippage_rate=0))
    opened = broker.on_signal("BTCUSDT", signal(), candle(1))
    closed = broker.on_candle(candle(2, open_=96, high=98, low=94, close=96))
    assert opened.status is PaperTradeStatus.OPEN
    assert closed.status is PaperTradeStatus.CLOSED_STOP_LOSS
    assert closed.net_pnl < 0
    assert broker.open_trade is None
    assert broker.summary()["trades_closed"] == 1


def test_stop_is_assumed_first_if_stop_and_target_hit_same_candle():
    broker = PaperBroker(PaperTradingPolicy(starting_balance=1000, fee_rate=0, slippage_rate=0))
    broker.on_signal("BTCUSDT", signal(), candle(1))
    closed = broker.on_candle(candle(2, high=111, low=94))
    assert closed.status is PaperTradeStatus.CLOSED_STOP_LOSS


def test_take_profit_closes_and_summary_tracks_net_pnl():
    broker = PaperBroker(PaperTradingPolicy(starting_balance=1000, fee_rate=0, slippage_rate=0))
    broker.on_signal("BTCUSDT", signal(), candle(1))
    closed = broker.on_candle(candle(2, high=111, low=99))
    assert closed.status is PaperTradeStatus.CLOSED_TAKE_PROFIT
    assert closed.net_pnl > 0
    assert broker.summary()["wins"] == 1
    assert broker.summary()["balance"] > 1000


def test_zero_balance_rejects_entry():
    broker = PaperBroker(PaperTradingPolicy(starting_balance=0))
    result = broker.on_signal("BTCUSDT", signal(), candle(1))
    assert result.status is PaperTradeStatus.REJECTED
    assert broker.open_trade is None

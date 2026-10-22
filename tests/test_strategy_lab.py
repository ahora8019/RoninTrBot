import math

import pytest

from app.core.strategy_lab import ClosedTradeResult, calculate_metrics


def trade(pnl, entry, exit):
    return ClosedTradeResult(net_pnl=pnl, entry_time=entry, exit_time=exit)


def test_metrics_use_net_pnl_and_compute_profit_factor():
    result = calculate_metrics(
        [trade(30, 1, 2), trade(-10, 3, 4), trade(20, 5, 6)],
        starting_balance=1000,
    )
    assert result.trades == 3
    assert result.wins == 2
    assert result.losses == 1
    assert result.net_pnl == 40
    assert result.profit_factor == 5
    assert result.win_rate == pytest.approx(2 / 3)
    assert result.expectancy == pytest.approx(40 / 3)


def test_drawdown_tracks_peak_to_trough_equity():
    result = calculate_metrics(
        [trade(100, 1, 2), trade(-50, 3, 4)],
        starting_balance=1000,
    )
    assert result.max_drawdown == pytest.approx(50 / 1100)


def test_metrics_sort_by_exit_time_not_input_order():
    result = calculate_metrics(
        [trade(-50, 3, 4), trade(100, 1, 2)],
        starting_balance=1000,
    )
    assert result.max_drawdown == pytest.approx(50 / 1100)


def test_no_losses_has_infinite_profit_factor_but_no_trades_has_none():
    profitable = calculate_metrics([trade(10, 1, 2)], starting_balance=100)
    empty = calculate_metrics([], starting_balance=100)
    assert math.isinf(profitable.profit_factor)
    assert empty.profit_factor is None
    assert empty.trades == 0
    assert empty.win_rate == 0


@pytest.mark.parametrize("balance", [0, -1, float("nan"), float("inf")])
def test_invalid_starting_balance_is_rejected(balance):
    with pytest.raises(ValueError, match="starting_balance"):
        calculate_metrics([], starting_balance=balance)


def test_non_finite_pnl_is_rejected():
    with pytest.raises(ValueError, match="trade_pnl"):
        calculate_metrics([trade(float("nan"), 1, 2)], starting_balance=100)


def test_exit_before_entry_is_rejected():
    with pytest.raises(ValueError, match="exit_before_entry"):
        calculate_metrics([trade(1, 10, 9)], starting_balance=100)

def test_metrics_from_paper_trades_only_include_closed_trades():
    from app.core.paper_trading import PaperTradeStatus
    from app.core.strategy_lab import metrics_from_paper_trades

    class Record:
        def __init__(self, status, pnl, entry, exit):
            self.status = status
            self.net_pnl = pnl
            self.entry_time = entry
            self.exit_time = exit

    result = metrics_from_paper_trades(
        [
            Record(PaperTradeStatus.CLOSED_TAKE_PROFIT, 12.5, 1, 2),
            Record(PaperTradeStatus.CLOSED_STOP_LOSS, -4.5, 2, 3),
            Record(PaperTradeStatus.OPEN, -1.0, 3, None),
            Record(PaperTradeStatus.REJECTED, 0.0, 4, None),
        ],
        starting_balance=100,
    )
    assert result.trades == 2
    assert result.net_pnl == 8.0
    assert result.profit_factor == pytest.approx(12.5 / 4.5)


def test_metrics_from_paper_trades_reject_closed_record_without_timestamps():
    from app.core.paper_trading import PaperTradeStatus
    from app.core.strategy_lab import metrics_from_paper_trades

    class IncompleteClosedRecord:
        status = PaperTradeStatus.CLOSED_TAKE_PROFIT
        net_pnl = 1.0
        entry_time = 1
        exit_time = None

    with pytest.raises(ValueError, match="closed_trade_missing_required_fields"):
        metrics_from_paper_trades([IncompleteClosedRecord()], starting_balance=100)


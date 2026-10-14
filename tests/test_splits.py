from decimal import Decimal

import pytest

from app.strategy.indicators import Candle
from app.validation.splits import chronological_split, walk_forward_windows


def make_candles(n=10):
    price = Decimal("100")
    return [
        Candle(i + 1, price, price + 1, price - 1, price, Decimal("10"))
        for i in range(n)
    ]


def test_chronological_split_proportions_and_order():
    split = chronological_split(make_candles(10), train_fraction=Decimal("0.7"))
    assert len(split.train) == 7
    assert len(split.gap) == 0
    assert len(split.test) == 3
    assert split.train[-1].timestamp < split.test[0].timestamp


def test_split_gap_is_excluded_between_train_and_test():
    split = chronological_split(make_candles(10), train_fraction=Decimal("0.7"), gap=1)
    assert len(split.train) == 7
    assert len(split.gap) == 1
    assert len(split.test) == 2
    all_ts = [c.timestamp for c in (*split.train, *split.gap, *split.test)]
    assert all_ts == sorted(all_ts)  # still chronological
    assert set(split.train).isdisjoint(split.test)


@pytest.mark.parametrize("bad", ["0", "1", Decimal("-0.5"), "abc", True, Decimal("1.5")])
def test_split_rejects_fraction_outside_open_unit_interval(bad):
    with pytest.raises(ValueError, match="train_fraction"):
        chronological_split(make_candles(), train_fraction=bad)


def test_split_fails_closed_when_a_side_would_be_too_small():
    with pytest.raises(ValueError, match="too few candles"):
        chronological_split(make_candles(10), train_fraction=Decimal("0.9"), min_size=2)
    with pytest.raises(ValueError, match="too few candles"):
        chronological_split(make_candles(3), train_fraction=Decimal("0.5"), gap=2)


def test_walk_forward_anchored_windows_grow_train():
    windows = walk_forward_windows(make_candles(10), train_size=4, test_size=2, step=2)
    assert len(windows) == 3
    assert [len(w.train) for w in windows] == [4, 6, 8]
    assert [len(w.test) for w in windows] == [2, 2, 2]
    # Each test slice starts strictly after its own train slice ends.
    for w in windows:
        assert w.train[-1].timestamp < w.test[0].timestamp


def test_walk_forward_rolling_mode_keeps_train_size_fixed():
    windows = walk_forward_windows(make_candles(10), train_size=4, test_size=2, step=2,
                                   anchored=False)
    assert [len(w.train) for w in windows] == [4, 4, 4]
    assert windows[1].train[0].timestamp == windows[0].train[2].timestamp


def test_walk_forward_default_step_equals_test_size():
    windows = walk_forward_windows(make_candles(9), train_size=3, test_size=3)
    assert [w.index for w in windows] == [0, 1]
    # Consecutive default-step windows are adjacent, not overlapping.
    assert windows[0].test[-1].timestamp + 1 == windows[1].test[0].timestamp


def test_walk_forward_drops_trailing_partial_test_window():
    # 10 candles, train 6, test 3 -> only one full window (6..8); the 9th candle
    # would form a partial test window and must not be evaluated.
    windows = walk_forward_windows(make_candles(10), train_size=6, test_size=3)
    assert len(windows) == 1
    assert len(windows[0].test) == 3


@pytest.mark.parametrize("kwargs", [
    {"train_size": 0, "test_size": 2},
    {"train_size": 2, "test_size": 0},
    {"train_size": 2, "test_size": 2, "step": 0},
    {"train_size": True, "test_size": 2},
])
def test_walk_forward_rejects_invalid_sizes(kwargs):
    with pytest.raises(ValueError):
        walk_forward_windows(make_candles(), **kwargs)

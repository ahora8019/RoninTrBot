"""Chronological out-of-sample splits and walk-forward windows.

Pure, deterministic helpers. They never reorder candles and never let a
train set overlap its corresponding test set. Validation is fail-closed:
ambiguous parameters raise instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.strategy.indicators import Candle, validate_candles


def _validate_int(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _fraction(value: object) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("train_fraction must be a finite decimal in (0, 1)") from None
    if not result.is_finite() or not Decimal("0") < result < Decimal("1"):
        raise ValueError("train_fraction must be a finite decimal in (0, 1)")
    return result


@dataclass(frozen=True, slots=True)
class Split:
    """One chronological split: train segment, excluded boundary gap, test segment."""
    train: tuple[Candle, ...]
    gap: tuple[Candle, ...]
    test: tuple[Candle, ...]


def chronological_split(
    candles: tuple[Candle, ...] | list[Candle],
    *,
    train_fraction: Decimal | str | int | float = Decimal("0.7"),
    gap: int = 0,
    min_size: int = 1,
) -> Split:
    """Split candles into earliest-train / latest-test with an optional excluded gap.

    The gap defaults to 0; raise it to quarantine boundary candles so that a
    test candle can never share information with the train segment.
    """
    data = validate_candles(tuple(candles), min_count=1)
    fraction = _fraction(train_fraction)
    gap = _validate_int(gap, "gap", minimum=0)
    min_size = _validate_int(min_size, "min_size", minimum=1)

    n = len(data)
    train_count = int(Decimal(n) * fraction)  # floor toward zero
    test_count = n - gap - train_count
    if train_count < min_size or test_count < min_size:
        raise ValueError(
            f"split of {n} candles at fraction {fraction} leaves too few candles "
            f"(train={train_count}, test={test_count}, gap={gap}, min_size={min_size})"
        )
    return Split(
        train=data[:train_count],
        gap=data[train_count:train_count + gap],
        test=data[train_count + gap:],
    )


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """One walk-forward window: a train slice and a later, non-overlapping test slice."""
    index: int
    train: tuple[Candle, ...]
    test: tuple[Candle, ...]


def walk_forward_windows(
    candles: tuple[Candle, ...] | list[Candle],
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    anchored: bool = True,
) -> tuple[WalkForwardWindow, ...]:
    """Emit chronological (train, test) windows over strictly ordered candles.

    anchored=True grows each train window from the first candle; anchored=False
    keeps a rolling fixed-size train window. Only FULL test windows are
    emitted: a trailing partial test window is dropped rather than evaluated
    on a tiny sample.
    """
    data = validate_candles(tuple(candles), min_count=1)
    train_size = _validate_int(train_size, "train_size", minimum=1)
    test_size = _validate_int(test_size, "test_size", minimum=1)
    if step is None:
        step = test_size
    step = _validate_int(step, "step", minimum=1)

    windows: list[WalkForwardWindow] = []
    start = train_size if anchored else train_size
    t0 = start
    index = 0
    while t0 + test_size <= len(data):
        train = data[:t0] if anchored else data[t0 - train_size:t0]
        test = data[t0:t0 + test_size]
        windows.append(WalkForwardWindow(index, train, test))
        index += 1
        t0 += step
    return tuple(windows)

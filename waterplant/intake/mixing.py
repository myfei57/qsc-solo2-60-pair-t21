"""Sample mixing applied before turbidity is judged."""

from __future__ import annotations

from collections.abc import Iterable


def mix(samples: Iterable[float]) -> float:
    """Average a sample window, returning zero when it is empty."""

    values = [float(value) for value in samples]
    if not values:
        return 0.0
    return sum(values) / len(values)

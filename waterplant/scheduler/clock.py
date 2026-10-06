"""Discrete time slots used by the backwash schedule board.

All scheduling happens in whole slots so the order inside one slot is explicit
(there is no "same time" ambiguity). The slot length is operator configurable;
the clock is injectable so the test suite can advance time deterministically.
"""

from __future__ import annotations

import time
from typing import Callable

DEFAULT_SLOT_SECONDS = 1800


class SlotClock:
    """Maps wall clock time onto non negative integer slots."""

    def __init__(
        self,
        slot_seconds: int = DEFAULT_SLOT_SECONDS,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        if slot_seconds <= 0:
            raise ValueError("slot_seconds must be positive")
        self._slot_seconds = slot_seconds
        self._time_fn = time_fn

    @property
    def slot_seconds(self) -> int:
        return self._slot_seconds

    def current(self) -> int:
        """Index of the slot that contains the current time."""

        return max(0, int(self._time_fn() // self._slot_seconds))

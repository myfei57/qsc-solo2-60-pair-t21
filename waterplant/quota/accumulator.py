"""Chlorine consumption accumulator with a periodic wrap."""

from __future__ import annotations

from waterplant.store.epoch import advance_epoch, load_float, save_float
from waterplant.store.store import Store

from .report import AccumState

ACCUM_KEY = "quota:chlorine-accumulator"
EPOCH_CAP = 100.0


class Accumulator:
    """Runs a wrapping total so the meter never overflows."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def add(self, amount: float) -> float:
        return advance_epoch(self._store, ACCUM_KEY, amount, EPOCH_CAP)

    def value(self) -> float:
        value, _ = load_float(self._store, ACCUM_KEY)
        return value

    def remaining(self, limit: float) -> float:
        remaining = limit - self.value()
        return remaining if remaining > 0 else 0.0

    def reset(self) -> None:
        save_float(self._store, ACCUM_KEY, 0.0)

    def state(self) -> AccumState:
        return AccumState(value=self.value())

    def describe(self) -> str:
        return f"quota accumulator value={self.value():.4f}"

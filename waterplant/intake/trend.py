"""Rolling window of intake flow readings."""

from __future__ import annotations

import json
from dataclasses import dataclass

from waterplant.store.epoch import load_float, save_float
from waterplant.store.store import Store

FLOW_TREND_KEY = "intake:trend:flow"
WINDOW_KEY = "intake:trend:window"
DEFAULT_WINDOW = 12
MAX_WINDOW = 1000


@dataclass(frozen=True)
class TrendStats:
    """Summary of the current window."""

    samples: int
    mean: float
    minimum: float
    maximum: float

    def as_dict(self) -> dict[str, float]:
        return {
            "samples": self.samples,
            "mean": self.mean,
            "minimum": self.minimum,
            "maximum": self.maximum,
        }


class Trend:
    """Keeps the most recent flow readings so dosing can see a trend."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def window(self) -> int:
        value, present = load_float(self._store, WINDOW_KEY)
        if not present or value <= 0:
            return DEFAULT_WINDOW
        return min(int(value), MAX_WINDOW)

    def set_window(self, value: int) -> int:
        if value <= 0:
            raise ValueError("window must be positive")
        if value > MAX_WINDOW:
            raise ValueError("window exceeds the supported range")
        save_float(self._store, WINDOW_KEY, float(value))
        self._store.put(FLOW_TREND_KEY, json.dumps(self.values()[-value:]))
        return value

    def values(self) -> list[float]:
        raw, present = self._store.get(FLOW_TREND_KEY)
        if not present:
            return []
        try:
            parsed = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(parsed, list):
            return []
        readings: list[float] = []
        for item in parsed:
            try:
                readings.append(float(item))
            except (TypeError, ValueError):
                continue
        return readings

    def record(self, value: float) -> TrendStats:
        """Append a reading and keep only the configured window."""

        readings = self.values()
        readings.append(float(value))
        readings = readings[-self.window() :]
        self._store.put(FLOW_TREND_KEY, json.dumps(readings))
        return self.stats()

    def stats(self) -> TrendStats:
        readings = self.values()
        if not readings:
            return TrendStats(samples=0, mean=0.0, minimum=0.0, maximum=0.0)
        return TrendStats(
            samples=len(readings),
            mean=sum(readings) / len(readings),
            minimum=min(readings),
            maximum=max(readings),
        )

    def reset(self) -> None:
        self._store.delete(FLOW_TREND_KEY)

    def describe(self) -> str:
        stats = self.stats()
        return f"intake trend samples={stats.samples} mean={stats.mean:.4f}"

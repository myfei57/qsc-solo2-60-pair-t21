"""Turbidity projections."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurbState:
    """Most recent turbidity verdict."""

    last_verdict: float

    def as_dict(self) -> dict[str, float]:
        return {"last_verdict": self.last_verdict}

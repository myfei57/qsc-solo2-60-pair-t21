"""Turbidity judgement feeding the coagulant dose."""

from __future__ import annotations

from collections.abc import Iterable

from waterplant.coag import Doser
from waterplant.intake import mix

from .report import TurbState

TURBIDITY_FLOOR = 1.0


def verdict_for(turbidity: float) -> float:
    """Collapse readings below the floor to a clean verdict."""

    if turbidity < TURBIDITY_FLOOR:
        return 0.0
    return turbidity


class Sampler:
    """Mixes a sample window before judging it."""

    def __init__(self, doser: Doser) -> None:
        self._doser = doser
        self._last = 0.0

    def judge(self, raw: Iterable[float]) -> float:
        mixed = mix(raw)
        verdict = verdict_for(mixed)
        self._last = verdict
        return self._doser.dose_for_turbidity(verdict)

    def state(self) -> TurbState:
        return TurbState(last_verdict=self._last)

    def describe(self) -> str:
        return f"turbidity last_verdict={self._last:.4f}"

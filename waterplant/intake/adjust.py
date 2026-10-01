"""Inlet and outlet valve movements used by the clear well loop."""

from __future__ import annotations


class InletController:
    """Moves a valve by an amount while keeping the level non-negative."""

    def raise_level(self, level: float, amount: float) -> float:
        return max(level + amount, 0.0)

    def lower_level(self, level: float, amount: float) -> float:
        return max(level - amount, 0.0)

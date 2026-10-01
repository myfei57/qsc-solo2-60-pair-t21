"""Quota projections and validation rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccumState:
    """Current accumulator value."""

    value: float

    def as_dict(self) -> dict[str, float]:
        return {"value": self.value}


def validate_amount(amount: float) -> None:
    """Reject amounts outside the supported range."""

    if amount < 0:
        raise ValueError("amount must be non-negative")
    if amount > 1_000_000:
        raise ValueError("amount exceeds the supported range")

"""Chlorine projections and validation rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChlorState:
    """Residual target together with the dose it implies."""

    target: float
    dose: float

    def as_dict(self) -> dict[str, float]:
        return {"target": self.target, "dose": self.dose}


def validate_demand(demand: float) -> None:
    """Reject residual demands outside the supported range."""

    if demand < 0:
        raise ValueError("demand must be non-negative")
    if demand > 1000:
        raise ValueError("demand exceeds the supported range")

"""Intake projections and validation rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlowState:
    """Everything the console needs to describe the intake."""

    flow: float
    turbidity: float
    present: bool

    def as_dict(self) -> dict[str, object]:
        return {"flow": self.flow, "turbidity": self.turbidity, "present": self.present}


def validate_flow(value: float) -> None:
    """Reject flow readings outside the supported instrument range."""

    if value < 0:
        raise ValueError("flow must be non-negative")
    if value > 1_000_000:
        raise ValueError("flow exceeds the supported range")

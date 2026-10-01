"""Clear well projections and validation rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WellState:
    """Stored level and residual target."""

    level: float
    residual_target: float

    def as_dict(self) -> dict[str, float]:
        return {"level": self.level, "residual_target": self.residual_target}


def validate_level(level: float) -> None:
    """Reject levels outside the supported range."""

    if level < 0:
        raise ValueError("level must be non-negative")
    if level > 1_000_000:
        raise ValueError("level exceeds the supported range")

"""Flow calibration projections and validation rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CalibState:
    """Current calibration factor."""

    factor: float

    def as_dict(self) -> dict[str, float]:
        return {"factor": self.factor}


def validate_factor(factor: float) -> None:
    """Reject calibration factors the meters cannot produce."""

    if factor <= 0:
        raise ValueError("factor must be positive")
    if factor > 100:
        raise ValueError("factor exceeds the supported range")

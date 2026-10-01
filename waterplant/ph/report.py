"""pH projections and validation rules."""

from __future__ import annotations

from dataclasses import dataclass

PH_BAND_LOW = 6.5
PH_BAND_HIGH = 8.5


@dataclass(frozen=True)
class PhState:
    """Latest pH reading together with the stable band it must sit in."""

    value: float
    stable: bool
    band_low: float
    band_high: float

    def as_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "stable": self.stable,
            "band_low": self.band_low,
            "band_high": self.band_high,
        }


def validate_ph(value: float) -> None:
    """Reject readings outside the physical pH scale."""

    if value < 0:
        raise ValueError("ph must be non-negative")
    if value > 14:
        raise ValueError("ph exceeds the supported range")

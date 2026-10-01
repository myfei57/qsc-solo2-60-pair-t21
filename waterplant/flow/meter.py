"""Physical flow meter replacement."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Meter:
    """A meter serial number paired with its calibration factor."""

    serial: str
    factor: float

    def as_dict(self) -> dict[str, object]:
        return {"serial": self.serial, "factor": self.factor}


def calibrate_meter(serial: str, factor: float) -> Meter:
    """Bind a factor to a meter so the swap can be audited."""

    return Meter(serial=serial, factor=factor)

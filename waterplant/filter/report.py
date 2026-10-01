"""Filter projections and validation rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BedState:
    """Read only copy of one filter bed."""

    id: str
    zone: int
    load: float
    closed: bool
    duty: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "zone": self.zone,
            "load": self.load,
            "closed": self.closed,
            "duty": self.duty,
        }


@dataclass(frozen=True)
class BankState:
    """Every bed plus which one currently holds duty."""

    beds: list[BedState]
    on_duty: str | None
    count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "beds": [bed.as_dict() for bed in self.beds],
            "on_duty": self.on_duty or "",
            "count": self.count,
        }


def validate_zone(zone: int) -> None:
    """Reject zone numbers outside the supported range."""

    if zone < 0:
        raise ValueError("zone must be non-negative")
    if zone > 100_000:
        raise ValueError("zone exceeds the supported range")

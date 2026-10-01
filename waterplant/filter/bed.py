"""A single filter bed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Bed:
    """Mutable state of one filter bed."""

    id: str
    zone: int
    load: float = 0.0
    closed: bool = False
    duty: bool = False

    def snapshot(self) -> dict[str, object]:
        return {
            "id": self.id,
            "zone": self.zone,
            "load": self.load,
            "closed": self.closed,
            "duty": self.duty,
        }

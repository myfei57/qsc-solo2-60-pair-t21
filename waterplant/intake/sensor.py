"""Raw readings captured at the intake."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sensor:
    """A single flow and turbidity observation."""

    flow: float
    turbidity: float

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "Sensor":
        return cls(
            flow=float(payload.get("flow", 0.0) or 0.0),
            turbidity=float(payload.get("turbidity", 0.0) or 0.0),
        )

    def as_dict(self) -> dict[str, float]:
        return {"flow": self.flow, "turbidity": self.turbidity}

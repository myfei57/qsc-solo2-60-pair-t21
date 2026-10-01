"""Coagulant projections."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DoseState:
    """Ratio and the flow value the next dose will follow."""

    ratio: float
    persisted_flow: float
    flow_present: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "ratio": self.ratio,
            "persisted_flow": self.persisted_flow,
            "flow_present": self.flow_present,
        }

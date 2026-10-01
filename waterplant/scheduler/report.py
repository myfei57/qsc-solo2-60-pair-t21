"""Scheduling projections and validation rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduleEntry:
    """One planned backwash with its position and drain duration."""

    bed_id: str
    load: float
    priority: int
    drain_seconds: int

    def as_dict(self) -> dict[str, object]:
        return {
            "bed_id": self.bed_id,
            "load": self.load,
            "priority": self.priority,
            "drain_seconds": self.drain_seconds,
        }


@dataclass(frozen=True)
class ScheduleState:
    """Threshold, due beds and the ordered plan."""

    threshold: float
    due: list[str]
    entries: list[ScheduleEntry]

    def as_dict(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "due": self.due,
            "entries": [entry.as_dict() for entry in self.entries],
        }


def validate_threshold(value: float) -> None:
    """Reject thresholds outside the supported load range."""

    if value <= 0:
        raise ValueError("threshold must be positive")
    if value > 1_000_000:
        raise ValueError("threshold exceeds the supported range")

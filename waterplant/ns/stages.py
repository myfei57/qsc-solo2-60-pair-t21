"""Named stages of the treatment line."""

from __future__ import annotations

from enum import Enum


class Stage(str, Enum):
    """A single processing step on the treatment line."""

    INTAKE = "intake"
    COAG = "coag"
    CHLOR = "chlor"
    FILTER = "filter"
    BACKWASH = "backwash"
    TURBIDITY = "turbidity"
    CLEARWELL = "clearwell"
    QUOTA = "quota"
    AUDIT = "audit"

    def __str__(self) -> str:
        return self.value

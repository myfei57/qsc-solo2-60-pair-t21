"""Static quota arithmetic."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Quota:
    """A chemical and the consumption limit allowed for it."""

    chemical: str
    limit: float


def check_quota(chemical: str, used: float, limit: float) -> tuple[float, bool]:
    """Return the remaining allowance and whether any is left."""

    if limit <= 0:
        return used, True
    remaining = limit - used
    return remaining, remaining >= 0

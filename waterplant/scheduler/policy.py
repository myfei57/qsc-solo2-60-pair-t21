"""Dispatch policies that must be fixed rather than left to ad hoc judgement.

Two ordering questions are answered here once and for all:

* ``rush_mode`` decides where an expedited ("加急") bed jumps. ``FRONT`` puts
  it at the very front of everything that has not started; ``ADVANCE_ONE``
  only promotes it one position. Already running work is never displaced.
* ``overlap`` decides who wins when windows overlap and capacity is tight.
  ``LOAD`` serves the dirtiest bed first; ``FCFS`` serves the bed that joined
  the queue first. Ties always fall back to first come first served and then
  to the bed identifier so the order is fully deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

FRONT = "front"
ADVANCE_ONE = "advance_one"
RUSH_MODES = (FRONT, ADVANCE_ONE)

LOAD = "load"
FCFS = "fcfs"
OVERLAP_POLICIES = (LOAD, FCFS)


@dataclass(frozen=True)
class Policy:
    """Operator chosen tie breaking rules."""

    rush_mode: str = FRONT
    overlap: str = LOAD

    def as_dict(self) -> dict[str, str]:
        return {"rush_mode": self.rush_mode, "overlap": self.overlap}


def validate_rush_mode(value: str) -> None:
    if value not in RUSH_MODES:
        raise ValueError(f"rush_mode must be one of {RUSH_MODES}")


def validate_overlap(value: str) -> None:
    if value not in OVERLAP_POLICIES:
        raise ValueError(f"overlap must be one of {OVERLAP_POLICIES}")

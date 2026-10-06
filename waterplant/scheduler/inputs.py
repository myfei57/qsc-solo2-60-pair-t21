"""Inputs the schedule board depends on: bed readings and wash windows.

A board rebuild needs two external facts for every bed:

* its current load and washability (the bed exists and can be closed), and
* the time window in which it may be washed plus how long the wash takes.

Either read can fail partially. Failure never aborts the whole rebuild: the
caller keeps the last known good value when one exists, otherwise that bed is
excluded, and every degraded bed is reported so the plan can be annotated and
audited.
"""

from __future__ import annotations

from dataclasses import dataclass

from waterplant.filter import Bank

DRAIN_BASE_SECONDS = 30
DRAIN_SECONDS_PER_LOAD = 5
DEFAULT_RELEASE_OFFSET = 0
DEFAULT_DEADLINE_OFFSET = 48


@dataclass(frozen=True)
class BedReading:
    """What the scheduler could learn about one bed at rebuild time."""

    bed_id: str
    load: float
    washable: bool
    load_stale: bool = False
    state_stale: bool = False

    @property
    def degraded(self) -> bool:
        return self.load_stale or self.state_stale

    def as_dict(self) -> dict[str, object]:
        return {
            "bed_id": self.bed_id,
            "load": self.load,
            "washable": self.washable,
            "load_stale": self.load_stale,
            "state_stale": self.state_stale,
        }


@dataclass(frozen=True)
class BedWindow:
    """Allowed wash window as slot offsets from the current slot."""

    release_offset: int = DEFAULT_RELEASE_OFFSET
    deadline_offset: int = DEFAULT_DEADLINE_OFFSET
    duration_slots: int = 1
    present: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "release_offset": self.release_offset,
            "deadline_offset": self.deadline_offset,
            "duration_slots": self.duration_slots,
            "present": self.present,
        }


def drain_slots_for_load(load: float, slot_seconds: int) -> int:
    """Mirror the legacy drain duration rule, rounded up to whole slots."""

    seconds = DRAIN_BASE_SECONDS + int(load * DRAIN_SECONDS_PER_LOAD)
    return max(1, -(-seconds // slot_seconds))


class BedInputs:
    """Reads live bed state from the filter bank.

    Subclasses or test doubles can override :meth:`read_load` /
    :meth:`read_washable` to raise and exercise the partial failure paths.
    """

    def __init__(self, bank: Bank, slot_seconds: int) -> None:
        self._bank = bank
        self._slot_seconds = slot_seconds

    def read_load(self, bed_id: str) -> float:
        bed = self._bank.bed(bed_id)
        if bed is None:
            raise LookupError(f"filter bed {bed_id} not found")
        return bed.load

    def read_washable(self, bed_id: str) -> bool:
        bed = self._bank.bed(bed_id)
        if bed is None:
            raise LookupError(f"filter bed {bed_id} not found")
        return not bed.closed

    def read(self, bed_id: str, last_load: float | None) -> BedReading:
        """Read one bed, falling back to its last known good load on failure."""

        load: float
        load_stale = False
        try:
            load = self.read_load(bed_id)
        except Exception:  # noqa: BLE001 - any read fault degrades, never crashes
            if last_load is None:
                raise
            load = last_load
            load_stale = True

        try:
            washable = self.read_washable(bed_id)
            state_stale = False
        except Exception:  # noqa: BLE001 - unreadable state: keep it out safely
            washable = False
            state_stale = True

        return BedReading(
            bed_id=bed_id,
            load=load,
            washable=washable,
            load_stale=load_stale,
            state_stale=state_stale,
        )

    def duration_slots(self, load: float, override: int | None) -> int:
        if override is not None and override > 0:
            return override
        return drain_slots_for_load(load, self._slot_seconds)

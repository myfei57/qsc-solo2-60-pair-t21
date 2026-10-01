"""The set of filter beds under one control console."""

from __future__ import annotations

import threading

from .bed import Bed
from .report import BankState, BedState


class Bank:
    """Thread safe registry of filter beds.

    Beds are stored by identifier. Lookups that mutate a bed are performed
    under the bank lock so concurrent console requests cannot interleave.
    """

    def __init__(self) -> None:
        self._beds: dict[str, Bed] = {}
        self._lock = threading.RLock()

    def add_bed(self, bed_id: str, zone: int, load: float = 0.0) -> Bed:
        with self._lock:
            bed = Bed(id=bed_id, zone=zone, load=load)
            self._beds[bed_id] = bed
            return bed

    def bed(self, bed_id: str) -> Bed | None:
        with self._lock:
            return self._beds.get(bed_id)

    def beds(self) -> list[Bed]:
        with self._lock:
            return [self._beds[key] for key in sorted(self._beds)]

    def remove_bed(self, bed_id: str) -> None:
        with self._lock:
            if bed_id not in self._beds:
                raise ValueError(f"filter bed {bed_id} not found")
            del self._beds[bed_id]

    def close(self, bed_id: str) -> None:
        with self._lock:
            self._require(bed_id).closed = True

    def open(self, bed_id: str) -> None:
        with self._lock:
            self._require(bed_id).closed = False

    def is_closed(self, bed_id: str) -> bool:
        with self._lock:
            return self._require(bed_id).closed

    def reset_closed(self) -> None:
        with self._lock:
            for bed in self._beds.values():
                bed.closed = False

    def set_load(self, bed_id: str, load: float) -> None:
        with self._lock:
            self._require(bed_id).load = load

    def renumber(self, bed_id: str, zone: int) -> None:
        with self._lock:
            self._require(bed_id).zone = zone

    def mapping(self) -> dict[int, str]:
        """Zone to bed identifier, rebuilt from the live bed registry."""

        with self._lock:
            return {bed.zone: bed_id for bed_id, bed in self._beds.items()}

    def bed_zone(self, bed_id: str) -> tuple[int, bool]:
        with self._lock:
            bed = self._beds.get(bed_id)
            if bed is None:
                return 0, False
            return bed.zone, True

    def rotate(self, bed_id: str) -> None:
        """Give the duty flag to one bed and clear it everywhere else."""

        with self._lock:
            self._require(bed_id)
            for bed in self._beds.values():
                bed.duty = False
            self._beds[bed_id].duty = True

    def on_duty(self) -> str | None:
        with self._lock:
            for bed_id, bed in self._beds.items():
                if bed.duty:
                    return bed_id
            return None

    def dirtiest(self) -> str:
        beds = self.beds()
        if not beds:
            return ""
        return max(beds, key=lambda bed: (bed.load, bed.id)).id

    def cleanest(self) -> str:
        beds = self.beds()
        if not beds:
            return ""
        return min(beds, key=lambda bed: (bed.load, bed.id)).id

    def count(self) -> int:
        with self._lock:
            return len(self._beds)

    def active_count(self) -> int:
        return sum(1 for bed in self.beds() if not bed.closed)

    def total_load(self) -> float:
        return sum(bed.load for bed in self.beds())

    def bed_ids(self) -> list[str]:
        return [bed.id for bed in self.beds()]

    def state(self) -> BankState:
        beds = self.beds()
        return BankState(
            beds=[
                BedState(
                    id=bed.id,
                    zone=bed.zone,
                    load=bed.load,
                    closed=bed.closed,
                    duty=bed.duty,
                )
                for bed in beds
            ],
            on_duty=self.on_duty(),
            count=len(beds),
        )

    def describe(self) -> str:
        state = self.state()
        return f"filter beds={state.count} on_duty={state.on_duty or ''}"

    def _require(self, bed_id: str) -> Bed:
        bed = self._beds.get(bed_id)
        if bed is None:
            raise ValueError(f"filter bed {bed_id} not found")
        return bed

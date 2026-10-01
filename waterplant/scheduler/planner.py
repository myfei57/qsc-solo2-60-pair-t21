"""Load driven backwash planner."""

from __future__ import annotations

from waterplant.filter import Bank
from waterplant.store.epoch import load_float, save_float
from waterplant.store.store import Store

from .report import ScheduleEntry, ScheduleState

THRESHOLD_KEY = "schedule:load-threshold"
DEFAULT_THRESHOLD = 6.0
DRAIN_BASE_SECONDS = 30
DRAIN_SECONDS_PER_LOAD = 5


class Scheduler:
    """Orders backwash work from the dirtiest bed down to the cleanest."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def threshold(self) -> float:
        value, present = load_float(self._store, THRESHOLD_KEY)
        return value if present else DEFAULT_THRESHOLD

    def set_threshold(self, value: float) -> float:
        save_float(self._store, THRESHOLD_KEY, value)
        return value

    def due(self, bank: Bank) -> list[str]:
        """Beds at or above the load threshold, dirtiest first."""

        threshold = self.threshold()
        loaded = [bed for bed in bank.beds() if bed.load >= threshold]
        loaded.sort(key=lambda bed: (-bed.load, bed.id))
        return [bed.id for bed in loaded]

    def plan(self, bank: Bank) -> list[ScheduleEntry]:
        """Number every bed by load so the operator drains the worst first."""

        ordered = sorted(bank.beds(), key=lambda bed: (-bed.load, bed.id))
        return [
            ScheduleEntry(
                bed_id=bed.id,
                load=bed.load,
                priority=index + 1,
                drain_seconds=DRAIN_BASE_SECONDS + int(bed.load * DRAIN_SECONDS_PER_LOAD),
            )
            for index, bed in enumerate(ordered)
        ]

    def state(self, bank: Bank) -> ScheduleState:
        return ScheduleState(
            threshold=self.threshold(),
            due=self.due(bank),
            entries=self.plan(bank),
        )

    def describe(self, bank: Bank) -> str:
        state = self.state(bank)
        return f"schedule threshold={state.threshold:.4f} due={len(state.due)}"

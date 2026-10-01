"""pH stabilisation gate that runs before chlorine is dosed.

The treatment order is fixed: the raw pH is corrected into the stable band
first, and only then may chlorine be applied. The stabiliser therefore
reports both the correction it wants and whether that correction is done.
"""

from __future__ import annotations

from dataclasses import dataclass

from waterplant.store.epoch import load_float, save_float
from waterplant.store.store import Store

from .report import PH_BAND_HIGH, PH_BAND_LOW, PhState

PH_KEY = "ph:value"
DEFAULT_PH = 7.0


@dataclass(frozen=True)
class PhVerdict:
    """The correction the stabiliser asks for and whether it is in band."""

    value: float
    stable: bool
    adjustment: float
    direction: str

    def as_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "stable": self.stable,
            "adjustment": self.adjustment,
            "direction": self.direction,
        }


class Stabilizer:
    """Holds the latest pH reading and decides the correction."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def current(self) -> float:
        value, present = load_float(self._store, PH_KEY)
        return value if present else DEFAULT_PH

    def read(self, value: float) -> PhVerdict:
        """Persist a new reading and evaluate it immediately."""

        save_float(self._store, PH_KEY, value)
        return self.stabilize()

    def is_stable(self) -> bool:
        return PH_BAND_LOW <= self.current() <= PH_BAND_HIGH

    def adjustment(self) -> float:
        """Correction amount: positive to raise pH, negative to lower it."""

        value = self.current()
        if value < PH_BAND_LOW:
            return PH_BAND_LOW - value
        if value > PH_BAND_HIGH:
            return PH_BAND_HIGH - value
        return 0.0

    def stabilize(self) -> PhVerdict:
        value = self.current()
        adjustment = self.adjustment()
        if adjustment > 0:
            direction = "raise"
        elif adjustment < 0:
            direction = "lower"
        else:
            direction = "hold"
        return PhVerdict(
            value=value,
            stable=adjustment == 0.0,
            adjustment=adjustment,
            direction=direction,
        )

    def state(self) -> PhState:
        return PhState(
            value=self.current(),
            stable=self.is_stable(),
            band_low=PH_BAND_LOW,
            band_high=PH_BAND_HIGH,
        )

    def describe(self) -> str:
        state = self.state()
        return (
            f"ph value={state.value:.4f} stable={state.stable} "
            f"band={state.band_low:.1f}-{state.band_high:.1f}"
        )

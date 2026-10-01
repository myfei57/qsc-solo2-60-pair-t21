"""Default values written on first boot."""

from __future__ import annotations

from waterplant.clearwell import Well
from waterplant.flow import Calibration
from waterplant.intake import DEFAULT_WINDOW, WINDOW_KEY, FlowRepository, Trend
from waterplant.ph import DEFAULT_PH, PH_KEY, Stabilizer
from waterplant.quota import Accumulator
from waterplant.scheduler import DEFAULT_THRESHOLD, THRESHOLD_KEY, Scheduler
from waterplant.store import Store, append_event, has_key

from .history import EVENT_KEY


def seed_defaults(store: Store) -> None:
    """Make sure each mutable component has a usable starting value."""

    calibration = Calibration(store)
    if calibration.current() <= 0:
        calibration.replace(1.0)

    well = Well(store)
    if well.residual_target() <= 0:
        well.set_residual_target(0.5)

    repository = FlowRepository(store)
    if not repository.load_flow()[1]:
        repository.persist_flow(1.0)

    accumulator = Accumulator(store)
    if accumulator.value() < 0:
        accumulator.reset()

    if not has_key(store, PH_KEY):
        Stabilizer(store).read(DEFAULT_PH)

    scheduler = Scheduler(store)
    if not has_key(store, THRESHOLD_KEY):
        scheduler.set_threshold(DEFAULT_THRESHOLD)

    trend = Trend(store)
    if not has_key(store, WINDOW_KEY):
        trend.set_window(DEFAULT_WINDOW)

    append_event(store, EVENT_KEY, "seed", "defaults")

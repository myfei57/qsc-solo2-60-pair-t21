"""Full component snapshot exposed by the console."""

from __future__ import annotations

from waterplant.ns import treatment_line
from waterplant.store import store_state

from .runtime import Runtime


def collect(rt: Runtime) -> dict[str, object]:
    """Combine every component projection into one document."""

    return {
        "pipeline": [step.as_dict() for step in treatment_line().steps()],
        "store": store_state(rt.store).as_dict(),
        "intake": rt.flow_repository.state().as_dict(),
        "coag": rt.coag_doser.state().as_dict(),
        "chlor": rt.chlor_doser.state().as_dict(),
        "filter": rt.bank.state().as_dict(),
        "backwash": rt.backwash.state().as_dict(),
        "turbidity": rt.sampler.state().as_dict(),
        "flow": rt.calibration.state().as_dict(),
        "clearwell": rt.well.state().as_dict(),
        "quota": rt.accumulator.state().as_dict(),
        "audit": rt.auditor.state().as_dict(),
        "ph": rt.stabilizer.state().as_dict(),
        "schedule": rt.scheduler.state(rt.bank).as_dict(),
        "trend": rt.trend.stats().as_dict(),
        "inventory": rt.inventory.state().as_dict(),
    }

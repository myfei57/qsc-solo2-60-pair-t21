"""Deterministic simulation ticks."""

from __future__ import annotations

from dataclasses import dataclass, field

from waterplant.ns import treatment_line

from .http import Request, Response, json_response
from .runtime import Runtime

DEFAULT_TICKS = 5
MAX_TICKS = 100
QUOTA_LIMIT = 100.0


@dataclass(frozen=True)
class Tick:
    """One simulated control step."""

    index: int
    dirtiest: str
    cleanest: str
    eligible: list[str]
    dose: float
    target: float
    remaining: float
    audit_kind: str

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "dirtiest": self.dirtiest,
            "cleanest": self.cleanest,
            "eligible": self.eligible,
            "dose": self.dose,
            "target": self.target,
            "remaining": self.remaining,
            "audit_kind": self.audit_kind,
        }


@dataclass(frozen=True)
class SimulationReport:
    """The full sequence plus closing counters."""

    ticks: list[Tick] = field(default_factory=list)
    final_audit_count: int = 0
    last_stage: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "ticks": [tick.as_dict() for tick in self.ticks],
            "final_audit_count": self.final_audit_count,
            "last_stage": self.last_stage,
        }


def _last_audit_kind(rt: Runtime) -> str:
    entry = rt.auditor.last()
    return "" if entry is None else entry.kind


def run_simulation(rt: Runtime, request: Request) -> Response:
    """Run a bounded number of ticks so the console can be exercised offline."""

    ticks = request.int_field("ticks", DEFAULT_TICKS)
    if ticks <= 0:
        ticks = DEFAULT_TICKS
    if ticks > MAX_TICKS:
        ticks = MAX_TICKS

    last_stage = treatment_line().last()
    steps = [
        Tick(
            index=index,
            dirtiest=rt.bank.dirtiest(),
            cleanest=rt.bank.cleanest(),
            eligible=rt.backwash.eligible(),
            dose=rt.coag_doser.dose_plan(float(index + 1), float(index)),
            target=rt.chlor_doser.target_for_demand(float(index)),
            remaining=rt.accumulator.remaining(QUOTA_LIMIT),
            audit_kind=_last_audit_kind(rt),
        )
        for index in range(ticks)
    ]
    report = SimulationReport(
        ticks=steps,
        final_audit_count=rt.auditor.count(),
        last_stage="" if last_stage is None else last_stage.value,
    )
    return json_response(report.as_dict())

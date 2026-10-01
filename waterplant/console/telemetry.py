"""Runtime telemetry counters."""

from __future__ import annotations

from dataclasses import dataclass

from .runtime import Runtime


@dataclass(frozen=True)
class Telemetry:
    """A flat snapshot of the numbers an operator watches."""

    store_keys: int
    filter_beds: int
    filter_active: int
    filter_load: float
    backwash_queue: int
    backwash_drains: int
    last_drain: str
    audit_entries: int
    quota_value: float
    clearwell_level: float
    coag_ratio: float
    chlor_target: float
    turbidity_verdict: float
    ph_value: float
    ph_stable: bool
    schedule_due: int
    trend_samples: int
    inventory_balance: float
    inventory_alerts: int

    def as_dict(self) -> dict[str, object]:
        return {
            "store_keys": self.store_keys,
            "filter_beds": self.filter_beds,
            "filter_active": self.filter_active,
            "filter_load": self.filter_load,
            "backwash_queue": self.backwash_queue,
            "backwash_drains": self.backwash_drains,
            "last_drain": self.last_drain,
            "audit_entries": self.audit_entries,
            "quota_value": self.quota_value,
            "clearwell_level": self.clearwell_level,
            "coag_ratio": self.coag_ratio,
            "chlor_target": self.chlor_target,
            "turbidity_verdict": self.turbidity_verdict,
            "ph_value": self.ph_value,
            "ph_stable": self.ph_stable,
            "schedule_due": self.schedule_due,
            "trend_samples": self.trend_samples,
            "inventory_balance": self.inventory_balance,
            "inventory_alerts": self.inventory_alerts,
        }


def collect(rt: Runtime) -> Telemetry:
    """Read every counter without mutating the control state."""

    return Telemetry(
        store_keys=rt.store.count(),
        filter_beds=rt.bank.count(),
        filter_active=rt.bank.active_count(),
        filter_load=rt.bank.total_load(),
        backwash_queue=rt.backwash.pending_count(),
        backwash_drains=rt.backwash.drain_count(),
        last_drain=rt.backwash.last_drain() or "",
        audit_entries=rt.auditor.count(),
        quota_value=rt.accumulator.value(),
        clearwell_level=rt.well.level(),
        coag_ratio=rt.coag_doser.current_ratio(),
        chlor_target=rt.chlor_doser.current_target(),
        turbidity_verdict=rt.sampler.state().last_verdict,
        ph_value=rt.stabilizer.current(),
        ph_stable=rt.stabilizer.is_stable(),
        schedule_due=len(rt.scheduler.due(rt.bank)),
        trend_samples=rt.trend.stats().samples,
        inventory_balance=rt.inventory.balance(),
        inventory_alerts=len(rt.inventory.needs_reorder()),
    )

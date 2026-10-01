"""Operator counters and store reset."""

from __future__ import annotations

from dataclasses import dataclass

from .runtime import Runtime


@dataclass(frozen=True)
class OpsCounters:
    """Small set of counters shown on the operator page."""

    store_keys: int
    flow_present: bool
    filter_beds: int
    backwash_queue: int
    audit_entries: int

    def as_dict(self) -> dict[str, object]:
        return {
            "store_keys": self.store_keys,
            "flow_present": self.flow_present,
            "filter_beds": self.filter_beds,
            "backwash_queue": self.backwash_queue,
            "audit_entries": self.audit_entries,
        }


def collect(rt: Runtime) -> OpsCounters:
    return OpsCounters(
        store_keys=rt.store.count(),
        flow_present=rt.flow_repository.load_flow()[1],
        filter_beds=rt.bank.count(),
        backwash_queue=rt.backwash.pending_count(),
        audit_entries=rt.auditor.count(),
    )


def reset_store(rt: Runtime) -> dict[str, object]:
    """Clear every persisted key, as a factory reset would."""

    rt.store.clear()
    return {"reset": True, "keys": rt.store.count()}

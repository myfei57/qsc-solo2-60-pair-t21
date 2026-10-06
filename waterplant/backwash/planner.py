"""Persistent, audited backwash scheduler."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from waterplant.filter import Bank
from waterplant.store.store import Store

from .planning import (
    EXPEDITE_FRONT,
    OVERLAP_LOAD_FIRST,
    Job,
    PlanResult,
    SlotPlan,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    STATUS_SCHEDULED,
    WaitingEntry,
    plan_jobs,
)

if TYPE_CHECKING:  # pragma: no cover - imported only for type checkers
    from waterplant.audit import Auditor

    from .controller import Controller

SCHEDULE_KEY = "backwash:schedule"
DEFAULT_CAPACITY = 1
MAX_HISTORY = 50


@dataclass
class Observation:
    """Last valid reading retained for partial-failure fallback."""

    load: float
    closed: bool

    def as_dict(self) -> dict[str, object]:
        return {"load": self.load, "closed": self.closed}


@dataclass
class ScheduleDocument:
    """The complete schedule aggregate stored in one atomic value."""

    jobs: dict[str, Job] = field(default_factory=dict)
    observations: dict[str, Observation] = field(default_factory=dict)
    history: list[Job] = field(default_factory=list)
    triggers: dict[str, str] = field(default_factory=dict)
    idempotency: dict[str, dict[str, str]] = field(default_factory=dict)
    current_slot: int = 0
    default_capacity: int = DEFAULT_CAPACITY
    capacity_by_slot: dict[int, int] = field(default_factory=dict)
    arrival_counter: int = 0
    expedite_policy: str = EXPEDITE_FRONT
    overlap_policy: str = OVERLAP_LOAD_FIRST
    last_result: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "jobs": {bed_id: job.as_dict() for bed_id, job in sorted(self.jobs.items())},
            "observations": {
                bed_id: observation.as_dict()
                for bed_id, observation in sorted(self.observations.items())
            },
            "history": [job.as_dict() for job in self.history[-MAX_HISTORY:]],
            "triggers": dict(sorted(self.triggers.items())),
            "idempotency": dict(sorted(self.idempotency.items())),
            "current_slot": self.current_slot,
            "default_capacity": self.default_capacity,
            "capacity_by_slot": {str(slot): value for slot, value in sorted(self.capacity_by_slot.items())},
            "arrival_counter": self.arrival_counter,
            "expedite_policy": self.expedite_policy,
            "overlap_policy": self.overlap_policy,
            "last_result": self.last_result,
        }


class BackwashScheduler:
    """Builds concrete crew slots and keeps them aligned with execution."""

    def __init__(
        self,
        store: Store,
        bank: Bank,
        controller: "Controller",
        auditor: "Auditor",
    ) -> None:
        self._store = store
        self._bank = bank
        self._controller = controller
        self._auditor = auditor
        self._lock = threading.RLock()

    def state(
        self,
        current_slot: int | None = None,
        capacity: int | None = None,
        capacity_by_slot: Mapping[int, int] | None = None,
    ) -> PlanResult:
        with self._lock:
            document = self._load()
            slot = document.current_slot if current_slot is None else current_slot
            crew = document.default_capacity if capacity is None else capacity
            self._validate_inputs(slot, crew, capacity_by_slot or {})
            return self._plan(document, slot, crew, capacity_by_slot or {})

    def trigger(
        self,
        request_id: str,
        demands: Mapping[str, Mapping[str, Any]],
        current_slot: int,
        capacity: int = DEFAULT_CAPACITY,
        capacity_by_slot: Mapping[int, int] | None = None,
    ) -> PlanResult:
        """Add or refresh demand, then persist one idempotent schedule result."""

        if not request_id:
            raise ValueError("request_id is required")
        self._validate_inputs(current_slot, capacity, capacity_by_slot or {})
        with self._lock:
            document = self._load()
            fingerprint = _fingerprint(
                {
                    "demands": demands,
                    "current_slot": current_slot,
                    "capacity": capacity,
                    "capacity_by_slot": dict(capacity_by_slot or {}),
                }
            )
            if request_id in document.triggers:
                if document.triggers[request_id] != fingerprint:
                    raise ValueError(f"request_id {request_id} was already used with different input")
                record = document.idempotency.get(request_id)
                if record is not None:
                    return self._stored_result(record)

            document.triggers[request_id] = fingerprint
            if current_slot >= document.current_slot:
                document.current_slot = current_slot
            document.default_capacity = capacity
            document.capacity_by_slot = dict(capacity_by_slot or {})

            for bed_id in demands:
                document.jobs[bed_id] = self._build_job(
                    document, bed_id, dict(demands[bed_id] or {})
                )

            result = self._plan(document, current_slot, capacity, capacity_by_slot or {})
            self._apply_result(document, result)
            document.last_result = result.as_dict()
            document.idempotency[request_id] = {
                "operation": "trigger",
                "fingerprint": self._result_key(result),
            }
            self._save(document)
            self._auditor.record(
                "backwash_schedule",
                json.dumps(
                    {
                        "request_id": request_id,
                        "current_slot": current_slot,
                        "capacity": capacity,
                        "partial_failure": result.partial_failure,
                        "waiting": [entry["bed_id"] for entry in result.as_dict()["waiting_order"]],
                    },
                    sort_keys=True,
                ),
            )
            return result

    def expedite(self, bed_id: str, request_id: str, urgent: bool = True) -> PlanResult:
        """Move only not-started work; running jobs remain locked."""

        if not request_id:
            raise ValueError("request_id is required")
        with self._lock:
            document = self._load()
            if request_id in document.idempotency:
                return self._stored_result(document.idempotency[request_id])
            job = document.jobs.get(bed_id)
            if job is None or job.status == STATUS_COMPLETED:
                raise ValueError(f"active backwash for {bed_id} not found")
            if job.status == STATUS_RUNNING:
                raise ValueError(f"running backwash {bed_id} is locked and cannot be expedited")
            job = document.jobs[bed_id]
            old_urgent = job.urgent
            old_status = job.status
            old_slot = job.scheduled_slot
            job.urgent = urgent
            result = self._plan(document, document.current_slot, document.default_capacity, document.capacity_by_slot)
            planned_job = result.job(bed_id)
            changed = (
                old_urgent != urgent
                or planned_job is not None
                and (old_status, old_slot) != (planned_job.status, planned_job.scheduled_slot)
            )
            self._apply_result(document, result)
            document.last_result = result.as_dict()
            document.idempotency[request_id] = {
                "operation": "expedite",
                "bed_id": bed_id,
                "fingerprint": self._result_key(result),
            }
            self._save(document)
            if changed:
                planned_job = result.job(bed_id)
                self._auditor.record(
                    "backwash_adjustment",
                    json.dumps(
                        {
                            "request_id": request_id,
                            "bed_id": bed_id,
                            "urgent": urgent,
                            "policy": EXPEDITE_FRONT,
                            "status": planned_job.status if planned_job else job.status,
                            "scheduled_slot": planned_job.scheduled_slot if planned_job else None,
                            "reasons": planned_job.reasons if planned_job else job.reasons,
                        },
                        sort_keys=True,
                    ),
                )
            return result

    def start(
        self,
        bed_id: str,
        execution_request_id: str,
        current_slot: int | None = None,
    ) -> PlanResult:
        """Start one scheduled/current job idempotently and lock it from replans."""

        if not execution_request_id:
            raise ValueError("execution_request_id is required")
        with self._lock:
            document = self._load()
            if execution_request_id in document.idempotency:
                return self._stored_result(document.idempotency[execution_request_id])
            slot = document.current_slot if current_slot is None else current_slot
            if slot < document.current_slot:
                raise ValueError("cannot start a backwash in an earlier slot")
            job = document.jobs.get(bed_id)
            if job is None or job.status == STATUS_COMPLETED:
                raise ValueError(f"scheduled backwash for {bed_id} not found")
            if job.status == STATUS_RUNNING:
                if self._controller.is_running(bed_id):
                    if job.execution_request_id == execution_request_id:
                        return self._plan(document, slot, document.default_capacity, document.capacity_by_slot)
                job.status = STATUS_PENDING
                job.scheduled_slot = None
                job.start_slot = None
                job.started_at = None
                job.execution_request_id = None
                job.closed = False
                job.reasons = _with_reason(job.reasons, "missing execution record; retrying start")
            if slot not in range(job.window_start, job.window_end + 1):
                raise ValueError(f"backwash {bed_id} is outside its time window")
            planned = self._plan(document, slot, document.default_capacity, document.capacity_by_slot)
            planned_job = planned.job(bed_id)
            if planned_job is None or planned_job.status != STATUS_SCHEDULED:
                raise ValueError(f"backwash {bed_id} is not scheduled to start")
            if planned_job.status == STATUS_SCHEDULED and planned_job.scheduled_slot != slot:
                raise ValueError(f"backwash {bed_id} is not scheduled for slot {slot}")
            current_running = [item for item in planned.slots[0].running if item != bed_id]
            if len(current_running) >= planned.slots[0].capacity:
                raise ValueError(f"crew capacity is full in slot {slot}")

            job.status = STATUS_RUNNING
            job.scheduled_slot = slot
            job.start_slot = slot
            job.started_at = _timestamp()
            job.execution_request_id = execution_request_id
            job.closed = True
            job.reasons = list(job.reasons)
            if "started; protected from later insertion" not in job.reasons:
                job.reasons.append("started; protected from later insertion")
            document.current_slot = slot
            self._save(document)
            self._controller.start(bed_id)
            result = self._plan(document, slot, document.default_capacity, document.capacity_by_slot)
            self._apply_result(document, result)
            document.last_result = result.as_dict()
            document.idempotency[execution_request_id] = {
                "operation": "start",
                "bed_id": bed_id,
                "fingerprint": self._result_key(result),
            }
            self._save(document)
            self._auditor.record(
                "backwash_start",
                json.dumps(
                    {
                        "request_id": execution_request_id,
                        "bed_id": bed_id,
                        "slot": slot,
                    },
                    sort_keys=True,
                ),
            )
            return result

    def complete(
        self,
        bed_id: str,
        execution_request_id: str,
        completion_request_id: str,
        current_slot: int | None = None,
    ) -> PlanResult:
        """Mark an actual drain complete and archive its schedule record."""

        if not completion_request_id:
            raise ValueError("completion_request_id is required")
        with self._lock:
            document = self._load()
            if completion_request_id in document.idempotency:
                return self._stored_result(document.idempotency[completion_request_id])
            slot = document.current_slot if current_slot is None else current_slot
            if slot < document.current_slot:
                raise ValueError("cannot complete a backwash in an earlier slot")
            job = document.jobs.get(bed_id)
            physical_running = self._controller.is_running(bed_id)
            if job is None or job.status != STATUS_RUNNING:
                if physical_running:
                    raise ValueError(f"backwash {bed_id} is recovering; use reconcile first")
                raise ValueError(f"backwash {bed_id} is not running")

            if physical_running:
                self._controller.complete(bed_id)
            job.status = STATUS_COMPLETED
            job.closed = False
            job.completed_slot = slot
            job.completed_at = _timestamp()
            job.reasons = list(job.reasons)
            if "completed" not in job.reasons:
                job.reasons.append("completed" if physical_running else "completed after reconciliation")
            document.history.append(job)
            del document.jobs[bed_id]
            document.current_slot = max(document.current_slot, slot)
            result = self._plan(document, document.current_slot, document.default_capacity, document.capacity_by_slot)
            self._apply_result(document, result)
            document.last_result = result.as_dict()
            document.idempotency[completion_request_id] = {
                "operation": "complete",
                "bed_id": bed_id,
                "fingerprint": self._result_key(result),
            }
            self._save(document)
            if physical_running:
                self._auditor.record(
                "backwash_complete",
                json.dumps(
                    {
                        "request_id": completion_request_id,
                        "execution_request_id": execution_request_id,
                        "bed_id": bed_id,
                        "slot": slot,
                    },
                    sort_keys=True,
                ),
            )
            return result

    def reconcile(self, current_slot: int | None = None) -> PlanResult:
        """Align stale schedule markers with the actual drain projection."""

        with self._lock:
            document = self._load()
            slot = document.current_slot if current_slot is None else current_slot
            result = self._plan(document, slot, document.default_capacity, document.capacity_by_slot)
            changed = [
                bed_id
                for bed_id, old_job in document.jobs.items()
                if (new_job := result.job(bed_id)) is not None
                and old_job.status == STATUS_RUNNING
                and new_job.status != STATUS_RUNNING
            ]
            serialized = result.as_dict()
            if changed or slot != document.current_slot or document.last_result != serialized:
                document.current_slot = slot
                self._apply_result(document, result)
                document.last_result = serialized
                self._save(document)
                if changed:
                    self._auditor.record(
                        "backwash_reconcile",
                        json.dumps({"slot": slot, "returned_to_queue": changed}, sort_keys=True)
                    )
            return result

    def _build_job(self, document: ScheduleDocument, bed_id: str, demand: Mapping[str, Any]) -> Job:
        window_start = int(demand.get("window_start", document.current_slot))
        window_end = int(demand.get("window_end", window_start))
        if window_end < window_start:
            raise ValueError(f"backwash window for {bed_id} ends before it starts")
        urgent = bool(demand.get("urgent", False))
        duration = int(demand.get("duration_slots", 1))
        if duration <= 0:
            raise ValueError(f"backwash duration for {bed_id} must be positive")

        live = self._bank.bed(bed_id)
        load_available = demand.get("load_available", True)
        state_available = demand.get("state_available", True)
        if not isinstance(load_available, bool) or not isinstance(state_available, bool):
            raise ValueError("load_available and state_available must be booleans")

        previous = document.observations.get(
            bed_id,
            Observation(load=0.0, closed=False),
        )

        if load_available:
            if live is not None:
                load = live.load
                load_status = "fresh"
            elif demand.get("load") is not None:
                load = float(demand["load"])
                load_status = "fresh"
            elif bed_id in document.observations:
                load = document.observations[bed_id].load
                load_status = "stale"
            else:
                load = 0.0
                load_status = "missing"
        elif bed_id in document.observations:
            load = document.observations[bed_id].load
            load_status = "stale"
        else:
            load = 0.0
            load_status = "missing"

        if state_available:
            if live is not None:
                closed = live.closed
                state_status = "fresh"
            elif "closed" in demand:
                closed = bool(demand["closed"])
                state_status = "fresh"
            elif bed_id in document.observations:
                closed = document.observations[bed_id].closed
                state_status = "stale"
            else:
                closed = False
                state_status = "missing"
        elif bed_id in document.observations:
            closed = document.observations[bed_id].closed
            state_status = "stale"
        else:
            closed = False
            state_status = "missing"

        if load_available or state_available:
            document.observations[bed_id] = Observation(
                load=load if load_status != "missing" else previous.load,
                closed=closed,
            )

        # Running physical state always comes from the actual drain projection.
        if self._controller.is_running(bed_id):
            closed = True
            state_status = "fresh"

        document.arrival_counter += 1
        existing = document.jobs.get(bed_id)
        request_id = str(demand.get("request_id", f"demand:{bed_id}:{document.arrival_counter}"))
        if existing is not None and existing.status != STATUS_COMPLETED:
            return Job(
                bed_id=bed_id,
                request_id=request_id,
                window_start=window_start,
                window_end=window_end,
                arrival_slot=existing.arrival_slot,
                arrival_seq=existing.arrival_seq,
                urgent=existing.urgent or urgent,
                duration_slots=duration,
                load=load,
                load_status=load_status,
                closed=closed,
                state_status=state_status,
                status=existing.status,
                scheduled_slot=existing.scheduled_slot,
                start_slot=existing.start_slot,
                started_at=existing.started_at,
                execution_request_id=existing.execution_request_id,
                reasons=["schedule refreshed"],
            )

        arrival_slot = int(demand.get("arrival_slot", document.current_slot))
        return Job(
            bed_id=bed_id,
            request_id=request_id,
            window_start=window_start,
            window_end=window_end,
            arrival_slot=arrival_slot,
            arrival_seq=document.arrival_counter,
            urgent=urgent,
            duration_slots=duration,
            load=load,
            load_status=load_status,
            closed=closed,
            state_status=state_status,
        )

    def _plan(
        self,
        document: ScheduleDocument,
        current_slot: int,
        capacity: int,
        capacity_by_slot: Mapping[int, int],
    ) -> PlanResult:
        actual_running = self._controller.drains()
        return plan_jobs(
            list(document.jobs.values()),
            current_slot,
            capacity,
            capacity_by_slot,
            actual_running=actual_running,
        )

    def _apply_result(self, document: ScheduleDocument, result: PlanResult) -> None:
        """Copy computed scheduling state back into the aggregate."""

        for job in result.jobs:
            if job.status != STATUS_COMPLETED:
                document.jobs[job.bed_id] = job

    def _load(self) -> ScheduleDocument:
        raw, present = self._store.get(SCHEDULE_KEY)
        if not present:
            return ScheduleDocument()
        try:
            payload = json.loads(raw)
        except ValueError:
            return ScheduleDocument()
        if not isinstance(payload, dict):
            return ScheduleDocument()
        return self._decode(payload)

    def _decode(self, payload: Mapping[str, Any]) -> ScheduleDocument:
        document = ScheduleDocument(
            triggers={str(key): str(value) for key, value in payload.get("triggers", {}).items()},
            idempotency={
                str(key): {str(k): str(v) for k, v in value.items()}
                for key, value in payload.get("idempotency", {}).items()
                if isinstance(value, dict)
            },
            current_slot=int(payload.get("current_slot", 0)),
            default_capacity=int(payload.get("default_capacity", DEFAULT_CAPACITY)),
            capacity_by_slot={
                int(slot): int(value)
                for slot, value in payload.get("capacity_by_slot", {}).items()
            },
            arrival_counter=int(payload.get("arrival_counter", 0)),
            expedite_policy=str(payload.get("expedite_policy", EXPEDITE_FRONT)),
            overlap_policy=str(payload.get("overlap_policy", OVERLAP_LOAD_FIRST)),
            last_result=payload.get("last_result", {}) if isinstance(payload.get("last_result", {}), dict) else {},
        )
        for bed_id, observation in payload.get("observations", {}).items():
            if isinstance(observation, dict):
                document.observations[str(bed_id)] = Observation(
                    load=float(observation.get("load", 0.0)),
                    closed=bool(observation.get("closed", False)),
                )
        for bed_id, job_payload in payload.get("jobs", {}).items():
            if isinstance(job_payload, dict):
                document.jobs[str(bed_id)] = _decode_job(str(bed_id), job_payload)
        for job_payload in payload.get("history", []):
            if isinstance(job_payload, dict):
                document.history.append(_decode_job(str(job_payload.get("bed_id", "")), job_payload))
        return document

    def _save(self, document: ScheduleDocument) -> None:
        self._store.put(SCHEDULE_KEY, json.dumps(document.as_dict(), ensure_ascii=False, sort_keys=True))

    def _result_key(self, result: PlanResult) -> str:
        return json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True)

    def _stored_result(self, record: Mapping[str, str]) -> PlanResult:
        if "fingerprint" not in record:
            document = self._load()
            return self._plan(
                document,
                document.current_slot,
                document.default_capacity,
                document.capacity_by_slot,
            )
        return _decode_result(json.loads(record["fingerprint"]))

    def _validate_inputs(
        self,
        current_slot: int,
        capacity: int,
        capacity_by_slot: Mapping[int, int],
    ) -> None:
        if current_slot < 0:
            raise ValueError("current slot must be non-negative")
        if capacity <= 0:
            raise ValueError("crew capacity must be positive")
        for slot, value in capacity_by_slot.items():
            if slot < 0 or value < 0:
                raise ValueError("slot capacity is invalid")


def _decode_job(bed_id: str, payload: Mapping[str, Any]) -> Job:
    return Job(
        bed_id=str(payload.get("bed_id", bed_id)),
        request_id=str(payload.get("request_id", "")),
        window_start=int(payload.get("window_start", 0)),
        window_end=int(payload.get("window_end", 0)),
        arrival_slot=int(payload.get("arrival_slot", 0)),
        arrival_seq=int(payload.get("arrival_seq", 0)),
        urgent=bool(payload.get("urgent", False)),
        duration_slots=int(payload.get("duration_slots", 1)),
        load=float(payload.get("load", 0.0)),
        load_status=str(payload.get("load_status", "fresh")),
        closed=bool(payload.get("closed", False)),
        state_status=str(payload.get("state_status", "fresh")),
        status=str(payload.get("status", "pending")),
        scheduled_slot=_optional_int(payload.get("scheduled_slot")),
        wait_priority=_optional_int(payload.get("wait_priority")),
        reasons=[str(item) for item in payload.get("reasons", [])],
        start_slot=_optional_int(payload.get("start_slot")),
        started_at=_optional_int(payload.get("started_at")),
        execution_request_id=_optional_str(payload.get("execution_request_id")),
        completed_slot=_optional_int(payload.get("completed_slot")),
        completed_at=_optional_int(payload.get("completed_at")),
    )


def _decode_result(payload: Mapping[str, Any]) -> PlanResult:
    jobs = tuple(
        _decode_job(str(item.get("bed_id", "")), item)
        for item in payload.get("jobs", [])
        if isinstance(item, dict)
    )
    slots = tuple(
        SlotPlan(
            slot=int(slot_payload.get("slot", 0)),
            capacity=int(slot_payload.get("capacity", 1)),
            running=tuple(str(item) for item in slot_payload.get("running", [])),
            scheduled=tuple(str(item) for item in slot_payload.get("scheduled", [])),
            waiting=tuple(
                WaitingEntry(
                    bed_id=str(item.get("bed_id", "")),
                    wait_priority=int(item.get("wait_priority", 0)),
                    load=float(item.get("load", 0.0)),
                    urgent=bool(item.get("urgent", False)),
                    arrival_seq=int(item.get("arrival_seq", 0)),
                    reasons=tuple(str(reason) for reason in item.get("reasons", [])),
                )
                for item in slot_payload.get("waiting", [])
                if isinstance(item, dict)
            ),
        )
        for slot_payload in payload.get("slots", [])
        if isinstance(slot_payload, dict)
    )
    return PlanResult(
        current_slot=int(payload.get("current_slot", 0)),
        horizon=int(payload.get("horizon", 0)),
        default_capacity=int(payload.get("default_capacity", 1)),
        slots=slots,
        jobs=jobs,
        partial_failure=bool(payload.get("partial_failure", False)),
        warnings=tuple(str(item) for item in payload.get("warnings", [])),
    )

def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _fingerprint(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _timestamp() -> int:
    from .schedule import now_unix

    return now_unix()

"""Pure backwash scheduling rules.

The planner in this module has no persistence side effects.  It takes a copy of
the active jobs and turns load, hard time windows, crew capacity and running
backwashes into concrete slots and waiting orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

EXPEDITE_FRONT = "front"
OVERLAP_LOAD_FIRST = "load_then_fcfs"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SCHEDULED = "scheduled"
STATUS_WAITING = "waiting"
STATUS_UPCOMING = "upcoming"
STATUS_BLOCKED = "blocked"
STATUS_COMPLETED = "completed"


@dataclass
class Job:
    """One active or completed backwash request."""

    bed_id: str
    request_id: str
    window_start: int
    window_end: int
    arrival_slot: int
    arrival_seq: int
    urgent: bool = False
    duration_slots: int = 1
    load: float = 0.0
    load_status: str = "fresh"
    closed: bool = False
    state_status: str = "fresh"
    status: str = STATUS_PENDING
    scheduled_slot: int | None = None
    wait_priority: int | None = None
    reasons: list[str] = field(default_factory=list)
    start_slot: int | None = None
    started_at: int | None = None
    execution_request_id: str | None = None
    completed_slot: int | None = None
    completed_at: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "bed_id": self.bed_id,
            "request_id": self.request_id,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "arrival_slot": self.arrival_slot,
            "arrival_seq": self.arrival_seq,
            "urgent": self.urgent,
            "duration_slots": self.duration_slots,
            "load": self.load,
            "load_status": self.load_status,
            "closed": self.closed,
            "state_status": self.state_status,
            "status": self.status,
            "scheduled_slot": self.scheduled_slot,
            "wait_priority": self.wait_priority,
            "reasons": list(self.reasons),
            "start_slot": self.start_slot,
            "started_at": self.started_at,
            "execution_request_id": self.execution_request_id,
            "completed_slot": self.completed_slot,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class WaitingEntry:
    """A job that could run in this slot but crew capacity is full."""

    bed_id: str
    wait_priority: int
    load: float
    urgent: bool
    arrival_seq: int
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "bed_id": self.bed_id,
            "wait_priority": self.wait_priority,
            "load": self.load,
            "urgent": self.urgent,
            "arrival_seq": self.arrival_seq,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class SlotPlan:
    """Crew allocation for one discrete scheduling slot."""

    slot: int
    capacity: int
    running: tuple[str, ...]
    scheduled: tuple[str, ...]
    waiting: tuple[WaitingEntry, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "slot": self.slot,
            "capacity": self.capacity,
            "running": list(self.running),
            "scheduled": list(self.scheduled),
            "waiting": [entry.as_dict() for entry in self.waiting],
        }


@dataclass(frozen=True)
class PlanResult:
    """Immutable result of applying scheduling rules to active jobs."""

    current_slot: int
    horizon: int
    default_capacity: int
    slots: tuple[SlotPlan, ...]
    jobs: tuple[Job, ...]
    partial_failure: bool
    warnings: tuple[str, ...]

    def job(self, bed_id: str) -> Job | None:
        for job in self.jobs:
            if job.bed_id == bed_id:
                return job
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "current_slot": self.current_slot,
            "horizon": self.horizon,
            "default_capacity": self.default_capacity,
            "slots": [slot.as_dict() for slot in self.slots],
            "jobs": [job.as_dict() for job in self.jobs],
            "waiting_order": [
                waiting.as_dict()
                for slot in self.slots
                for waiting in slot.waiting
            ],
            "partial_failure": self.partial_failure,
            "warnings": list(self.warnings),
            "policy": {
                "expedite": EXPEDITE_FRONT,
                "overlap": OVERLAP_LOAD_FIRST,
            },
        }


def job_priority_key(job: Job) -> tuple[int, float, int, str]:
    """Fixed overlap policy: urgent front, then higher load, then FCFS."""

    return (0 if job.urgent else 1, -job.load, job.arrival_seq, job.bed_id)


def is_blocked(job: Job, current_slot: int) -> bool:
    return (
        job.load_status == "missing"
        or job.state_status == "missing"
        or (job.closed and job.status != STATUS_RUNNING)
        or job.window_end < current_slot
    )


def plan_jobs(
    jobs: Sequence[Job],
    current_slot: int,
    default_capacity: int = 1,
    capacity_by_slot: Mapping[int, int] | None = None,
    actual_running: Sequence[str] = (),
) -> PlanResult:
    """Recompute all not-started jobs without mutating the supplied jobs."""

    if current_slot < 0:
        raise ValueError("current slot must be non-negative")
    if default_capacity <= 0:
        raise ValueError("crew capacity must be positive")

    capacities = dict(capacity_by_slot or {})
    for slot, capacity in capacities.items():
        if slot < 0 or capacity < 0:
            raise ValueError("slot capacity is invalid")

    planned: dict[str, Job] = {}
    completed: list[Job] = []
    warnings: list[str] = []
    partial_failure = False
    actual = set(actual_running)

    for source in jobs:
        job = replace(
            source,
            reasons=list(source.reasons),
            scheduled_slot=None,
            wait_priority=None,
            closed=(source.status == STATUS_RUNNING and source.bed_id in actual),
        )
        if job.window_end < job.window_start:
            raise ValueError(f"backwash window for {job.bed_id} ends before it starts")
        if job.duration_slots <= 0:
            raise ValueError(f"backwash duration for {job.bed_id} must be positive")
        if job.status == STATUS_COMPLETED:
            completed.append(job)
            continue
        planned[job.bed_id] = job

    max_window_end = current_slot
    for job in planned.values():
        max_window_end = max(max_window_end, job.window_end)
        if job.load_status in {"stale", "missing"}:
            partial_failure = True
            warnings.append(_data_warning(job, "load"))
        if job.state_status in {"stale", "missing"}:
            partial_failure = True
            warnings.append(_data_warning(job, "state"))

    # Work occupying future slots.  Running work is locked.  Earlier planned
    # assignments are also counted so a long job reserves its whole duration.
    occupied: dict[int, set[str]] = {}
    for bed_id in actual:
        occupied.setdefault(current_slot, set()).add(bed_id)
        if bed_id not in planned:
            warnings.append(f"actual backwash {bed_id} has no schedule record")

    for bed_id, source in list(planned.items()):
        if source.status != STATUS_RUNNING:
            continue
        if bed_id not in actual:
            # The execution projection is authoritative; a stale running marker
            # returns to the not-started queue.
            planned[bed_id] = replace(
                source,
                status=STATUS_PENDING,
                scheduled_slot=None,
                start_slot=None,
                started_at=None,
                execution_request_id=None,
                reasons=_with_reason(source.reasons, "missing execution record; returned to queue"),
            )
            warnings.append(f"stale running record removed for {bed_id}")
            continue
        start_slot = source.start_slot if source.start_slot is not None else current_slot
        end_slot = start_slot + source.duration_slots
        for slot in range(max(current_slot, start_slot), end_slot):
            occupied.setdefault(slot, set()).add(bed_id)
        planned[bed_id] = replace(
            source,
            status=STATUS_RUNNING,
            scheduled_slot=start_slot,
            reasons=_with_reason(source.reasons, "running job is locked"),
        )

    slot_plans: list[SlotPlan] = []
    first_waiting: dict[str, WaitingEntry] = {}
    unassigned = {
        bed_id
        for bed_id, job in planned.items()
        if job.status != STATUS_RUNNING
        and job.load_status != "missing"
        and job.state_status != "missing"
        and not job.closed
        and job.window_end >= current_slot
    }

    for slot in range(current_slot, max_window_end + 1):
        capacity = int(capacities.get(slot, default_capacity))
        occupancy = set(occupied.get(slot, set()))
        eligible = [
            planned[bed_id]
            for bed_id in unassigned
            if planned[bed_id].window_start <= slot <= planned[bed_id].window_end
        ]
        eligible.sort(key=job_priority_key)

        scheduled: list[str] = []
        waiting: list[WaitingEntry] = []
        used = set(occupancy)
        for position, job in enumerate(eligible, start=1):
            if len(used) < capacity:
                assigned = replace(
                    job,
                    status=STATUS_SCHEDULED,
                    scheduled_slot=slot,
                    reasons=_scheduled_reasons(
                        job, slot, current_slot, job.bed_id in first_waiting
                    ),
                )
                planned[job.bed_id] = assigned
                unassigned.remove(job.bed_id)
                scheduled.append(job.bed_id)
                used.add(job.bed_id)
                for future_slot in range(slot, slot + job.duration_slots):
                    occupied.setdefault(future_slot, set()).add(job.bed_id)
            else:
                reasons = _waiting_reasons(job)
                entry = WaitingEntry(
                    bed_id=job.bed_id,
                    wait_priority=position,
                    load=job.load,
                    urgent=job.urgent,
                    arrival_seq=job.arrival_seq,
                    reasons=tuple(reasons),
                )
                waiting.append(entry)
                first_waiting.setdefault(job.bed_id, entry)

        slot_plans.append(
            SlotPlan(
                slot=slot,
                capacity=capacity,
                running=tuple(sorted(bed for bed in occupancy)),
                scheduled=tuple(scheduled),
                waiting=tuple(waiting),
            )
        )

    for bed_id, job in list(planned.items()):
        if job.status == STATUS_RUNNING:
            continue
        if is_blocked(job, current_slot):
            reasons = list(job.reasons)
            if job.load_status == "missing" or job.state_status == "missing":
                reasons = _with_reason(reasons, "last valid data unavailable")
            if job.closed:
                reasons = _with_reason(reasons, "bed is closed")
            if job.window_end < current_slot:
                reasons = _with_reason(reasons, "time window expired")
            planned[bed_id] = replace(job, status=STATUS_BLOCKED, scheduled_slot=None, reasons=reasons)
        elif bed_id not in unassigned:
            planned[bed_id] = replace(
                job,
                status=STATUS_SCHEDULED,
                wait_priority=None,
            )
        elif job.window_start > current_slot and job.bed_id not in first_waiting:
            planned[bed_id] = replace(
                job,
                status=STATUS_UPCOMING,
                scheduled_slot=None,
                wait_priority=None,
                reasons=_with_reason(job.reasons, "time window has not opened"),
            )
        else:
            entry = first_waiting.get(bed_id)
            planned[bed_id] = replace(
                job,
                status=STATUS_WAITING,
                scheduled_slot=None,
                wait_priority=entry.wait_priority if entry else 1,
                reasons=list(entry.reasons) if entry else ["waiting for crew capacity"],
            )

    ordered_jobs = tuple(sorted(planned.values(), key=_result_sort_key)) + tuple(completed)
    return PlanResult(
        current_slot=current_slot,
        horizon=max_window_end,
        default_capacity=default_capacity,
        slots=tuple(slot_plans),
        jobs=ordered_jobs,
        partial_failure=partial_failure,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _scheduled_reasons(job: Job, slot: int, current_slot: int, waited: bool = False) -> list[str]:
    reasons = list(job.reasons)
    if job.urgent:
        reasons = _with_reason(reasons, "urgent request inserted before not-started work")
    if slot > current_slot or waited:
        reasons = _with_reason(reasons, "current crew capacity full; deferred to available slot")
    if job.load_status == "stale" or job.state_status == "stale":
        reasons = _with_reason(reasons, "scheduled from last valid reading")
    return reasons or ["scheduled within time window"]


def _waiting_reasons(job: Job) -> list[str]:
    reasons = list(job.reasons)
    reasons = _with_reason(reasons, "waiting for crew capacity")
    if job.urgent:
        reasons = _with_reason(reasons, "urgent position among waiting jobs")
    if job.load_status == "stale" or job.state_status == "stale":
        reasons = _with_reason(reasons, "last valid reading")
    return reasons


def _data_warning(job: Job, kind: str) -> str:
    status = getattr(job, f"{kind}_status")
    if status == "missing":
        return f"{kind} unavailable for {job.bed_id}; no last valid value"
    return f"{kind} unavailable for {job.bed_id}; using last valid value"


def _with_reason(reasons: Sequence[str], reason: str) -> list[str]:
    result = list(reasons)
    if reason not in result:
        result.append(reason)
    return result


def _result_sort_key(job: Job) -> tuple[int, int, float, int, str]:
    status_rank = {
        STATUS_RUNNING: 0,
        STATUS_SCHEDULED: 1,
        STATUS_WAITING: 2,
        STATUS_UPCOMING: 3,
        STATUS_BLOCKED: 4,
        STATUS_PENDING: 5,
    }.get(job.status, 9)
    slot = job.scheduled_slot if job.scheduled_slot is not None else 10**12
    return status_rank, slot, -job.load, job.arrival_seq, job.bed_id

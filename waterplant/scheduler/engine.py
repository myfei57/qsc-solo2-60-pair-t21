"""Pure scheduling engine: job ordering and capacity aware slot assignment.

The engine has no persistence and no side effects. It takes candidate jobs,
the slots already reserved by running work, the crew capacity per slot and the
current slot, and decides which slot each job starts in. Jobs that do not fit
stay in the waiting queue in the order they should be released.
"""

from __future__ import annotations

from dataclasses import dataclass

from .inputs import BedReading, BedWindow
from .policy import FRONT, FCFS, LOAD, Policy

WAITING = "waiting"
RUNNING = "running"
DONE = "done"


@dataclass
class Job:
    """One backwash position on the board."""

    bed_id: str
    load: float
    arrival: int
    release: int
    deadline: int
    duration: int
    release_offset: int = 0
    deadline_offset: int = 0
    rush: bool = False
    status: str = WAITING
    start_slot: int | None = None
    load_stale: bool = False
    state_stale: bool = False

    @property
    def is_waiting(self) -> bool:
        return self.status == WAITING and self.start_slot is None

    def queue_key(self, policy: Policy):
        """Order among jobs that have not started.

        Under the ``front`` rush mode rush work always leads. Under
        ``advance_one`` the rush flag does not reorder: the one position
        promotion is a manual move applied on top of the policy order. After
        that the overlap policy decides: dirtiest first, or first come first
        served; arrival and bed id remain deterministic tie breakers.
        """

        rush_rank = 0 if (self.rush and policy.rush_mode == FRONT) else 1
        if policy.overlap == LOAD:
            return (rush_rank, -self.load, self.arrival, self.bed_id)
        if policy.overlap == FCFS:
            return (rush_rank, self.arrival, self.bed_id)
        raise ValueError(f"unknown overlap policy {policy.overlap}")

    def as_dict(self, current: int) -> dict[str, object]:
        waiting = self.is_waiting
        return {
            "bed_id": self.bed_id,
            "load": self.load,
            "arrival": self.arrival,
            "release": self.release,
            "deadline": self.deadline,
            "duration": self.duration,
            "rush": self.rush,
            "status": "ready" if waiting and self.release <= current else self.status,
            "waiting": waiting,
            "start_slot": self.start_slot,
            "load_stale": self.load_stale,
            "state_stale": self.state_stale,
        }


def candidate(
    reading: BedReading,
    window: BedWindow,
    current: int,
    arrival: int,
    duration: int,
) -> Job:
    """Build a fresh waiting job from one bed reading and its window."""

    release = current + max(0, window.release_offset)
    deadline = current + max(window.release_offset, window.deadline_offset)
    return Job(
        bed_id=reading.bed_id,
        load=reading.load,
        arrival=arrival,
        release=release,
        deadline=deadline,
        duration=duration,
        release_offset=window.release_offset,
        deadline_offset=window.deadline_offset,
        load_stale=reading.load_stale,
        state_stale=reading.state_stale,
    )


def order_waiting(jobs: list[Job], policy: Policy) -> list[Job]:
    """Sort jobs that have not started, keeping rush work ahead."""

    return sorted(jobs, key=lambda job: job.queue_key(policy))


def assign_slots(
    jobs: list[Job],
    capacity: int,
    reserved: dict[int, int],
    current: int,
    horizon: int,
) -> None:
    """Reserve the earliest feasible slot window for every waiting job.

    ``reserved`` maps a slot to how many crews running work already occupies
    there. Jobs are considered in queue order; each one takes the earliest run
    of ``duration`` free slots inside its window. A job that cannot fit keeps
    ``start_slot = None`` and is returned to the caller in the waiting queue.
    """

    busy: dict[int, int] = dict(reserved)
    horizon_end = current + max(1, horizon) - 1
    for job in jobs:
        if job.status != WAITING or job.start_slot is not None:
            continue
        latest_start = min(job.deadline, horizon_end) - job.duration + 1
        start = None
        for candidate_slot in range(max(current, job.release), latest_start + 1):
            if all(
                busy.get(slot, 0) < capacity
                for slot in range(candidate_slot, candidate_slot + job.duration)
            ):
                start = candidate_slot
                break
        if start is None:
            continue
        job.start_slot = start
        for slot in range(start, start + job.duration):
            busy[slot] = busy.get(slot, 0) + 1


def waiting_queue(jobs: list[Job], policy: Policy) -> list[Job]:
    """Jobs without a start slot, in the order operators should release them."""

    return order_waiting([job for job in jobs if job.is_waiting], policy)

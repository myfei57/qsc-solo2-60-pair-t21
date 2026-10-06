"""Capacity and window aware backwash schedule board.

The board is the single persisted document that binds the plan to execution:
jobs, the execution ledger and dispatch policy are committed together so a
crash can never leave the schedule disagreeing with what was actually washed.

Responsibilities:

* rebuild the plan from live bed readings, time windows and crew capacity,
  degrading to last known good values on partial read failure,
* expedite ("加急") beds without disturbing work that has already started,
* start and finish washes through the backwash controller and keep the
  execution ledger consistent with it,
* stay idempotent: repeated triggers and repeated requests repeat nothing.
"""

from __future__ import annotations

import json
import time

from waterplant.audit import Auditor
from waterplant.store.store import Store

from .clock import SlotClock
from .engine import DONE, RUNNING, WAITING, Job, assign_slots, candidate as build_candidate
from .engine import order_waiting
from .inputs import DEFAULT_DEADLINE_OFFSET, DEFAULT_RELEASE_OFFSET, BedInputs, BedWindow
from .planner import DEFAULT_THRESHOLD, THRESHOLD_KEY
from .policy import FRONT, Policy, validate_overlap, validate_rush_mode
from .report import validate_capacity, validate_horizon

BOARD_KEY = "schedule:board"
WINDOWS_KEY = "schedule:windows"
MAX_ADJUSTMENTS = 50


class ScheduleBoard:
    """Owns the committed backwash plan and its execution ledger."""

    def __init__(
        self,
        store: Store,
        inputs: BedInputs,
        auditor: Auditor,
        clock: SlotClock | None = None,
        capacity: int = 2,
        horizon: int = DEFAULT_DEADLINE_OFFSET,
        policy: Policy | None = None,
    ) -> None:
        self._store = store
        self._inputs = inputs
        self._auditor = auditor
        self._clock = clock or SlotClock()
        self._doc = self._load()
        self._doc["capacity"] = int(self._doc.get("capacity", capacity))
        self._doc["horizon"] = int(self._doc.get("horizon", horizon))
        self._doc["policy"] = self._doc.get("policy") or (policy or Policy()).as_dict()

    # ------------------------------------------------------------------ config

    @property
    def slot_seconds(self) -> int:
        return self._clock.slot_seconds

    def capacity(self) -> int:
        return int(self._doc["capacity"])

    def set_capacity(self, value: int) -> int:
        validate_capacity(value)
        self._doc["capacity"] = int(value)
        self._save()
        self._adjust("capacity", f"crew capacity -> {value}")
        return value

    def horizon(self) -> int:
        return int(self._doc["horizon"])

    def set_horizon(self, value: int) -> int:
        validate_horizon(value)
        self._doc["horizon"] = int(value)
        self._save()
        self._adjust("horizon", f"planning horizon -> {value} slots")
        return value

    def policy(self) -> Policy:
        raw = self._doc["policy"]
        return Policy(rush_mode=raw.get("rush_mode", FRONT), overlap=raw.get("overlap", "load"))

    def set_policy(self, rush_mode: str | None = None, overlap: str | None = None) -> Policy:
        current = self.policy()
        rush_mode = rush_mode or current.rush_mode
        overlap = overlap or current.overlap
        validate_rush_mode(rush_mode)
        validate_overlap(overlap)
        self._doc["policy"] = Policy(rush_mode=rush_mode, overlap=overlap).as_dict()
        self._save()
        self._adjust("policy", f"rush_mode={rush_mode} overlap={overlap}")
        return self.policy()

    def threshold(self) -> float:
        raw, present = self._store.get(THRESHOLD_KEY)
        try:
            return float(raw) if present else DEFAULT_THRESHOLD
        except ValueError:
            return DEFAULT_THRESHOLD

    # ----------------------------------------------------------------- windows

    def set_window(
        self,
        bed_id: str,
        release_offset: int | None = None,
        deadline_offset: int | None = None,
        duration_slots: int | None = None,
    ) -> BedWindow:
        windows = self._windows()
        current = windows.get(
            bed_id,
            BedWindow(DEFAULT_RELEASE_OFFSET, DEFAULT_DEADLINE_OFFSET, 1).as_dict(),
        )
        if release_offset is not None:
            current["release_offset"] = release_offset
        if deadline_offset is not None:
            current["deadline_offset"] = deadline_offset
        if duration_slots is not None:
            if duration_slots < 1:
                raise ValueError("duration_slots must be at least one")
            current["duration_slots"] = duration_slots
        if current["deadline_offset"] < current["release_offset"]:
            raise ValueError("deadline_offset must not precede release_offset")
        current["present"] = True
        windows[bed_id] = current
        self._store.put(WINDOWS_KEY, json.dumps(windows, ensure_ascii=False))
        self._adjust("window", f"{bed_id} window {json.dumps(current, ensure_ascii=False)}")
        return BedWindow(**current)

    def window_for(self, bed_id: str) -> BedWindow:
        return BedWindow(**self._windows().get(bed_id, BedWindow().as_dict()))

    def _windows(self) -> dict[str, dict[str, object]]:
        raw, present = self._store.get(WINDOWS_KEY)
        if not present:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(key): value for key, value in parsed.items() if isinstance(value, dict)}

    # ------------------------------------------------------------------ build

    def rebuild(
        self,
        bed_ids: list[str],
        threshold: float,
        trigger_id: str | None = None,
    ) -> dict[str, object]:
        """Recompute waiting assignments from live inputs.

        Repeating a trigger (or a trigger-less rebuild while nothing changed)
        is a no-op and returns the existing generation.
        """

        if trigger_id and trigger_id in self._doc["triggers"]:
            return self._result(rebuilt=False, reason="duplicate trigger")

        current = self._clock.current()
        jobs = self._reconstruct_jobs(current)
        existing = {job.bed_id: job for job in jobs if job.status == WAITING}
        last_good = self._doc["last_good"]

        candidates: list[Job] = []
        stale: list[dict[str, object]] = []
        for bed_id in bed_ids:
            last_load = last_good.get(bed_id)
            try:
                reading = self._inputs.read(bed_id, None if last_load is None else float(last_load))
            except Exception as exc:  # noqa: BLE001 - exclude beds with no good value
                stale.append({"bed_id": bed_id, "reason": f"unreadable: {exc}", "excluded": True})
                continue
            if reading.load_stale:
                stale.append({"bed_id": bed_id, "reason": "load stale", "excluded": False})
            if reading.state_stale:
                stale.append({"bed_id": bed_id, "reason": "state stale", "excluded": True})
                continue
            if not reading.washable or reading.load < threshold:
                continue
            last_good[bed_id] = reading.load
            window = self.window_for(bed_id)
            duration = self._inputs.duration_slots(
                reading.load,
                None
                if window.duration_slots == 1 and not window.present
                else window.duration_slots,
            )
            if bed_id in existing:
                job = existing[bed_id]
                job.load = reading.load
                job.load_stale = reading.load_stale
                job.duration = duration
                job.release = current + max(0, window.release_offset)
                job.deadline = current + max(window.release_offset, window.deadline_offset)
                job.release_offset = window.release_offset
                job.deadline_offset = window.deadline_offset
                # a slot in the past cannot still be the start; re-plan it
                if job.start_slot is not None and job.start_slot < current:
                    job.start_slot = None
                candidates.append(job)
                continue
            self._doc["arrival"] = int(self._doc.get("arrival", 0)) + 1
            candidates.append(build_candidate(reading, window, current, self._doc["arrival"], duration))

        ordered = self._merge_into_queue(jobs, candidates)
        reserved = self._reserved_slots(jobs)
        assign_slots(ordered, self.capacity(), reserved, current, self.horizon())

        waiting = [job for job in ordered if job.status == WAITING]
        active = [job for job in jobs if job.status in (RUNNING, DONE)]
        new_jobs = active + waiting
        changed = self._signature(new_jobs, current) != self._doc.get("signature")
        if changed or stale:
            self._doc["jobs"] = [self._job_to_dict(job) for job in new_jobs]
            self._doc["last_good"] = last_good
            self._doc["last_stale"] = bool(stale)
            self._doc["current_slot"] = current
            self._doc["signature"] = self._signature(new_jobs, current)
            self._doc["generation"] = int(self._doc.get("generation", 0)) + 1
            if trigger_id:
                self._doc["triggers"].append(trigger_id)
                self._doc["triggers"] = self._doc["triggers"][-200:]
            self._save()
            if stale:
                self._auditor.record("schedule.partial", json.dumps(stale, ensure_ascii=False))
            self._adjust(
                "rebuild",
                f"generation={self._doc['generation']} waiting={len(waiting)} stale={len(stale)}",
            )
            return self._result(rebuilt=True, reason="planned", stale=stale)
        if self._doc.get("last_stale"):
            self._doc["last_stale"] = False
            self._save()
        if trigger_id:
            self._doc["triggers"].append(trigger_id)
            self._doc["triggers"] = self._doc["triggers"][-200:]
            self._save()
        return self._result(rebuilt=False, reason="unchanged", stale=stale)

    # ------------------------------------------------------------------- rush

    def rush(self, bed_id: str, request_id: str | None = None) -> dict[str, object]:
        """Expedite a bed that has not started yet.

        ``FRONT`` moves it ahead of every other waiting bed; ``ADVANCE_ONE``
        swaps it one position forward. Running and finished work is untouched.
        """

        request_key = f"rush:{bed_id}:{request_id}" if request_id else None
        if request_key and request_key in self._doc["requests"]:
            return self._result(rebuilt=False, reason="duplicate request")

        jobs = self._reconstruct_jobs(self._clock.current())
        # rush positions are manual: operate on the stored queue order rather
        # than re-sorting, otherwise one-position promotion cannot be expressed
        waiting = [job for job in jobs if job.status == WAITING]
        index = next((i for i, job in enumerate(waiting) if job.bed_id == bed_id), None)
        if index is None:
            if any(job.bed_id == bed_id for job in jobs if job.status in (RUNNING, DONE)):
                raise ValueError(f"bed {bed_id} already started; rush cannot disturb it")
            raise ValueError(f"bed {bed_id} is not on the waiting board")

        mode = self.policy().rush_mode
        if mode == FRONT:
            target = waiting.pop(index)
            target.rush = True
            waiting.insert(0, target)
            movement = f"{bed_id} -> front of waiting queue"
        else:
            if index == 0:
                waiting[index].rush = True
                movement = f"{bed_id} already at the front"
            else:
                waiting[index].rush = True
                waiting[index - 1], waiting[index] = waiting[index], waiting[index - 1]
                movement = f"{bed_id} advanced one position ({index + 1} -> {index})"

        current = self._clock.current()
        reserved = self._reserved_slots(jobs)
        for job in waiting:
            job.release = current + max(0, job.release_offset)
            job.deadline = current + max(job.release_offset, job.deadline_offset)
            if job.start_slot is not None and job.start_slot < current:
                job.start_slot = None
        assign_slots(waiting, self.capacity(), reserved, current, self.horizon())
        new_jobs = [job for job in jobs if job.status in (RUNNING, DONE)] + waiting
        self._commit_jobs(new_jobs, current)
        if request_key:
            self._doc["requests"].append(request_key)
            self._doc["requests"] = self._doc["requests"][-200:]
            self._save()
        self._adjust("rush", f"mode={mode} {movement}")
        self._auditor.record(
            "schedule.rush", json.dumps({"bed_id": bed_id, "mode": mode}, ensure_ascii=False)
        )
        return self._result(rebuilt=True, reason=movement)

    # --------------------------------------------------------------- execution

    def start(self, bed_id: str, request_id: str | None = None) -> dict[str, object]:
        """Start a wash for a bed the committed plan says is due now.

        The board document and the execution ledger are committed before the
        controller acts; if the controller rejects the start the commit is
        rolled back so the two records can never diverge.
        """

        if request_id:
            prior = next(
                (
                    record
                    for record in self._doc["executions"]
                    if record.get("request_id") == request_id
                ),
                None,
            )
            if prior is not None:
                return self._result(rebuilt=False, reason="duplicate request", bed=bed_id,
                                    start_slot=prior.get("start_slot"))

        current = self._clock.current()
        jobs = self._reconstruct_jobs(current)
        job = next((item for item in jobs if item.bed_id == bed_id), None)
        if job is None or job.status != WAITING:
            raise ValueError(f"bed {bed_id} is not waiting on the board")
        if job.release > current:
            raise ValueError(f"bed {bed_id} window opens in slot {job.release}")

        active = sum(1 for slot in range(current, current + job.duration)
                     if self._usage_at(jobs, slot) >= self.capacity())
        if active:
            raise ValueError(f"no free crew for bed {bed_id} in slot {current}")

        snapshot = json.dumps(self._doc, ensure_ascii=False)
        job.status = RUNNING
        job.start_slot = current
        self._commit_jobs(jobs, current)
        record = {
            "bed_id": bed_id,
            "start_slot": current,
            "finish_slot": None,
            "request_id": request_id,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._doc["executions"].append(record)
        self._save()

        controller = getattr(self, "_controller", None)
        if controller is not None:
            try:
                controller.start(bed_id)
            except Exception:
                self._doc = json.loads(snapshot)
                self._save()
                self._reopen_bed(bed_id)
                raise
        self._adjust("start", f"{bed_id} running in slot {current}")
        self._auditor.record("schedule.start", json.dumps({"bed_id": bed_id}, ensure_ascii=False))
        return self._result(rebuilt=True, reason="started", bed=bed_id, start_slot=current)

    def finish(self, bed_id: str, request_id: str | None = None) -> dict[str, object]:
        """Close out a running wash and reopen the bed for service."""

        request_key = f"finish:{bed_id}:{request_id}" if request_id else None
        if request_key and request_key in self._doc["requests"]:
            return self._result(rebuilt=False, reason="duplicate request", bed=bed_id)

        current = self._clock.current()
        jobs = self._reconstruct_jobs(current)
        job = next((item for item in jobs if item.bed_id == bed_id), None)
        if job is None or job.status != RUNNING:
            raise ValueError(f"bed {bed_id} is not running")

        record = next(
            (
                item
                for item in reversed(self._doc["executions"])
                if item["bed_id"] == bed_id and item["finish_slot"] is None
            ),
            None,
        )
        if record is not None:
            record["finish_slot"] = current
        job.status = DONE
        self._commit_jobs(jobs, current)
        controller = getattr(self, "_controller", None)
        if controller is not None:
            self._reopen_bed(bed_id)
        if request_key:
            self._doc["requests"].append(request_key)
            self._doc["requests"] = self._doc["requests"][-200:]
        self._save()
        self._adjust("finish", f"{bed_id} completed in slot {current}")
        self._auditor.record("schedule.finish", json.dumps({"bed_id": bed_id}, ensure_ascii=False))
        return self._result(rebuilt=True, reason="finished", bed=bed_id)

    def bind_controller(self, controller) -> None:
        """Attach the backwash controller used for real starts."""

        self._controller = controller

    def _reopen_bed(self, bed_id: str) -> None:
        reopen = getattr(self._controller, "_bank", None)
        if reopen is not None and reopen.bed(bed_id) is not None:
            reopen.open(bed_id)

    # ------------------------------------------------------------- reconciliation

    def reconcile(self) -> dict[str, object]:
        """Compare the ledger against beds the controller actually drained."""

        drains = []
        controller = getattr(self, "_controller", None)
        if controller is not None:
            drains = controller.drains()
        jobs = self._reconstruct_jobs(self._clock.current())
        mismatches: list[str] = []
        for job in jobs:
            if job.status == RUNNING and job.bed_id not in drains:
                mismatches.append(f"{job.bed_id} running on board but not drained in field")
        ledger_running = {
            item["bed_id"]
            for item in self._doc["executions"]
            if item["finish_slot"] is None
        }
        for bed_id in ledger_running:
            if bed_id not in drains:
                mismatches.append(f"{bed_id} ledger open without a field drain")
        for bed_id in drains:
            if bed_id not in ledger_running and not any(
                job.bed_id == bed_id and job.status == DONE for job in jobs
            ):
                mismatches.append(f"{bed_id} drained in field without a board start")
        consistent = not mismatches
        return {"consistent": consistent, "mismatches": mismatches, "drains": drains}

    # ------------------------------------------------------------------ views

    def state(self) -> dict[str, object]:
        current = self._clock.current()
        jobs = self._reconstruct_jobs(current)
        # waiting jobs are shown in the committed order: that is the sequence
        # operators must release them in, including manual rush promotions
        waiting = [job for job in jobs if job.status == WAITING]
        running = [job for job in jobs if job.status == RUNNING]
        done = [job for job in jobs if job.status == DONE]
        return {
            "current_slot": current,
            "slot_seconds": self.slot_seconds,
            "capacity": self.capacity(),
            "horizon": self.horizon(),
            "threshold": self.threshold(),
            "policy": self.policy().as_dict(),
            "generation": self._doc.get("generation", 0),
            "stale": bool(self._doc.get("last_stale")),
            "running": [job.as_dict(current) for job in running],
            "jobs": [job.as_dict(current) for job in waiting + running + done],
            "waiting_queue": [job.bed_id for job in waiting if job.start_slot is None],
            "assignments": [
                {
                    "bed_id": job.bed_id,
                    "start_slot": job.start_slot,
                    "rush": job.rush,
                    "load_stale": job.load_stale,
                }
                for job in sorted(
                    [item for item in jobs if item.start_slot is not None],
                    key=lambda item: (item.start_slot, item.bed_id),
                )
            ],
            "executions": list(self._doc["executions"]),
            "adjustments": list(self._doc["adjustments"]),
            "reconcile": self.reconcile(),
        }

    def describe(self) -> str:
        state = self.state()
        return (
            f"board slot={state['current_slot']} capacity={state['capacity']} "
            f"running={len(state['running'])} waiting={len(state['waiting_queue'])} "
            f"generation={state['generation']}"
        )

    # ------------------------------------------------------------- persistence

    def _load(self) -> dict[str, object]:
        raw, present = self._store.get(BOARD_KEY)
        empty = {
            "jobs": [],
            "executions": [],
            "adjustments": [],
            "triggers": [],
            "requests": [],
            "last_good": {},
            "arrival": 0,
            "generation": 0,
            "current_slot": self._clock.current(),
            "signature": "",
            "last_stale": False,
        }
        if not present:
            return empty
        try:
            doc = json.loads(raw)
        except ValueError:
            return empty
        if not isinstance(doc, dict):
            return empty
        for key, value in empty.items():
            doc.setdefault(key, value)
        return doc

    def _save(self) -> None:
        self._store.put(BOARD_KEY, json.dumps(self._doc, ensure_ascii=False, sort_keys=True))

    def _job_to_dict(self, job: Job) -> dict[str, object]:
        return {
            "bed_id": job.bed_id,
            "load": job.load,
            "arrival": job.arrival,
            "release": job.release,
            "deadline": job.deadline,
            "duration": job.duration,
            "release_offset": job.release_offset,
            "deadline_offset": job.deadline_offset,
            "rush": job.rush,
            "status": job.status,
            "start_slot": job.start_slot,
            "load_stale": job.load_stale,
            "state_stale": job.state_stale,
        }

    def _reconstruct_jobs(self, current: int) -> list[Job]:
        jobs: list[Job] = []
        for raw in self._doc["jobs"]:
            if not isinstance(raw, dict) or "bed_id" not in raw:
                continue
            jobs.append(
                Job(
                    bed_id=str(raw["bed_id"]),
                    load=float(raw.get("load", 0.0)),
                    arrival=int(raw.get("arrival", 0)),
                    release=int(raw.get("release", current)),
                    deadline=int(raw.get("deadline", current)),
                    duration=int(raw.get("duration", 1)),
                    release_offset=int(raw.get("release_offset", 0)),
                    deadline_offset=int(raw.get("deadline_offset", 0)),
                    rush=bool(raw.get("rush", False)),
                    status=str(raw.get("status", WAITING)),
                    start_slot=raw.get("start_slot"),
                    load_stale=bool(raw.get("load_stale", False)),
                    state_stale=bool(raw.get("state_stale", False)),
                )
            )
        return jobs

    def _commit_jobs(self, jobs: list[Job], current: int) -> None:
        self._doc["jobs"] = [self._job_to_dict(job) for job in jobs]
        self._doc["current_slot"] = current
        self._doc["signature"] = self._signature(jobs, current)
        self._doc["generation"] = int(self._doc.get("generation", 0)) + 1

    def _merge_into_queue(self, jobs: list[Job], candidates: list[Job]) -> list[Job]:
        """Keep committed waiting order and insert new jobs by policy key.

        Manual promotions (rush) live in the stored order, so a rebuild must
        not re-sort the queue. Beds that reappear keep their slot; genuinely
        new beds are inserted at the first position they would beat under the
        overlap policy, at the end when none apply.
        """

        policy = self.policy()
        candidate_ids = {candidate.bed_id for candidate in candidates}
        committed = [job for job in jobs if job.status == WAITING and job.bed_id in candidate_ids]
        order = list(committed)
        committed_ids = {job.bed_id for job in committed}
        for job in order_waiting(
            [candidate for candidate in candidates if candidate.bed_id not in committed_ids], policy
        ):
            position = next(
                (index for index, current in enumerate(order) if job.queue_key(policy) < current.queue_key(policy)),
                len(order),
            )
            order.insert(position, job)
        return order

    def _reserved_slots(self, jobs: list[Job]) -> dict[int, int]:
        reserved: dict[int, int] = {}
        for job in jobs:
            if job.status == RUNNING and job.start_slot is not None:
                for slot in range(job.start_slot, job.start_slot + job.duration):
                    reserved[slot] = reserved.get(slot, 0) + 1
        return reserved

    def _usage_at(self, jobs: list[Job], slot: int) -> int:
        count = 0
        for job in jobs:
            if job.status != RUNNING or job.start_slot is None:
                continue
            if job.start_slot <= slot < job.start_slot + job.duration:
                count += 1
        return count

    def _signature(self, jobs: list[Job], current: int) -> str:
        parts = [
            f"{job.bed_id}:{job.status}:{job.start_slot}:{job.rush}:{job.release}:{job.deadline}"
            for job in jobs
        ]
        return f"{current}|" + ",".join(parts)

    def _adjust(self, kind: str, reason: str) -> None:
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kind": kind,
            "reason": reason,
            "generation": self._doc.get("generation", 0),
        }
        self._doc["adjustments"].append(entry)
        self._doc["adjustments"] = self._doc["adjustments"][-MAX_ADJUSTMENTS:]
        self._save()
        self._auditor.record(
            "schedule.adjust", json.dumps({"kind": kind, "reason": reason}, ensure_ascii=False)
        )

    def _result(self, rebuilt: bool, reason: str, **extra) -> dict[str, object]:
        result = {
            "rebuilt": rebuilt,
            "reason": reason,
            "generation": self._doc.get("generation", 0),
            "current_slot": self._clock.current(),
        }
        result.update(extra)
        return result

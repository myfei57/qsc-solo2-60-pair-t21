"""Behavioural tests for the capacity and window aware schedule board."""

from __future__ import annotations

import tempfile
import unittest

from waterplant.audit import Auditor
from waterplant.backwash import Controller
from waterplant.filter import Bank
from waterplant.scheduler import (
    ADVANCE_ONE,
    FCFS,
    BedInputs,
    Policy,
    ScheduleBoard,
    SlotClock,
)
from waterplant.store import Store


class ManualClock:
    """Callable time source the tests can freeze and step."""

    def __init__(self, slot: int = 100) -> None:
        self.slot = slot

    def __call__(self) -> float:
        return float(self.slot * 60)


class FlakyInputs(BedInputs):
    """Bed inputs whose reads can be switched off per bed."""

    def __init__(self, bank: Bank, slot_seconds: int) -> None:
        super().__init__(bank, slot_seconds)
        self.load_down: set[str] = set()
        self.state_down: set[str] = set()

    def read_load(self, bed_id: str) -> float:
        if bed_id in self.load_down:
            raise RuntimeError("load telemetry unavailable")
        return super().read_load(bed_id)

    def read_washable(self, bed_id: str) -> bool:
        if bed_id in self.state_down:
            raise RuntimeError("state telemetry unavailable")
        return super().read_washable(bed_id)


def build_board(tmp: str, capacity: int = 2, policy: Policy | None = None):
    store = Store.open(f"{tmp}/state.json")
    bank = Bank()
    auditor = Auditor(store)
    controller = Controller(bank, store)
    clock = SlotClock(slot_seconds=60, time_fn=ManualClock(100))
    inputs = FlakyInputs(bank, clock.slot_seconds)
    board = ScheduleBoard(
        store, inputs, auditor, clock=clock, capacity=capacity, policy=policy or Policy()
    )
    board.bind_controller(controller)
    store.put("schedule:load-threshold", "5.0")
    return store, bank, auditor, controller, clock, inputs, board


class ScheduleBoardCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        (
            self.store,
            self.bank,
            self.auditor,
            self.controller,
            self.clock,
            self.inputs,
            self.board,
        ) = build_board(self._tmp.name)

    def beds(self) -> None:
        self.bank.add_bed("b1", 1, 9.0)
        self.bank.add_bed("b2", 2, 8.0)
        self.bank.add_bed("b3", 3, 7.0)
        self.bank.add_bed("b4", 4, 4.0)  # below threshold

    def test_load_window_and_capacity_decide_order(self) -> None:
        self.beds()
        result = self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.assertTrue(result["rebuilt"])
        state = self.board.state()
        # b4 is below threshold; the rest are ordered by descending load
        planned = [job["bed_id"] for job in state["jobs"] if job["status"] == "waiting"]
        self.assertEqual(planned[:3], ["b1", "b2", "b3"])
        by_bed = {item["bed_id"]: item["start_slot"] for item in state["assignments"]}
        self.assertEqual(by_bed["b1"], 100)
        self.assertEqual(by_bed["b2"], 100)  # capacity 2 shares slot 100
        self.assertEqual(by_bed["b3"], 102)  # two-slot wash, third starts after
        self.assertEqual(state["waiting_queue"], [])

    def test_overflow_becomes_an_ordered_waiting_queue(self) -> None:
        self.bank.add_bed("b1", 1, 9.0)
        self.bank.add_bed("b2", 2, 9.0)
        self.bank.add_bed("b3", 3, 9.0)
        # tight windows: b3 can only run in slot 100, which the two heavier
        # jobs take, so it must wait with a clear queue position
        self.board.set_window("b1", 0, 0, 1)
        self.board.set_window("b2", 0, 0, 1)
        self.board.set_window("b3", 0, 0, 1)
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        state = self.board.state()
        self.assertEqual(state["waiting_queue"], ["b3"])
        queued = next(job for job in state["jobs"] if job["bed_id"] == "b3")
        self.assertTrue(queued["waiting"])
        self.assertEqual(queued["status"], "ready")

    def test_release_window_gates_earliest_start(self) -> None:
        self.beds()
        self.board.set_window("b1", release_offset=3, deadline_offset=5)
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        b1 = next(item for item in self.board.state()["assignments"] if item["bed_id"] == "b1")
        self.assertEqual(b1["start_slot"], 103)

    def test_rush_front_never_moves_running_work(self) -> None:
        self.beds()
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.board.start("b1", "s1")
        # expedite the lightest eligible bed: it must jump past b2/b3...
        self.board.rush("b3", "r1")
        state = self.board.state()
        running = [job["bed_id"] for job in state["running"]]
        self.assertEqual(running, ["b1"])
        waiting = [job["bed_id"] for job in state["jobs"] if job["status"] == "waiting"]
        self.assertEqual(waiting[0], "b3")
        # ... but cannot displace b1 from the crews already at work
        self.assertEqual(self.controller.command_list(), [])

    def test_rush_advance_one_only_promotes_one_position(self) -> None:
        self.beds()
        board = ScheduleBoard(
            self.store,
            self.inputs,
            self.auditor,
            clock=self.clock,
            capacity=1,
            policy=Policy(rush_mode=ADVANCE_ONE),
        )
        board.bind_controller(self.controller)
        board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        board.rush("b3", "r1")
        waiting = [
            job["bed_id"]
            for job in board.state()["jobs"]
            if job["status"] == "waiting"
        ]
        # b3 swaps exactly one position (with b2); b1 at the front is untouched
        self.assertEqual(waiting[:3], ["b1", "b3", "b2"])
    def test_rush_on_started_bed_is_rejected(self) -> None:
        self.beds()
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.board.start("b1", "s1")
        with self.assertRaises(ValueError):
            self.board.rush("b1", "r2")

    def test_trigger_and_request_repeats_are_idempotent(self) -> None:
        self.beds()
        first = self.board.rebuild(self.bank.bed_ids(), 5.0, "dup")
        second = self.board.rebuild(self.bank.bed_ids(), 5.0, "dup")
        self.assertTrue(first["rebuilt"])
        self.assertFalse(second["rebuilt"])
        self.assertEqual(first["generation"], second["generation"])
        started = self.board.start("b1", "start-dup")
        repeated = self.board.start("b1", "start-dup")
        self.assertEqual(started["start_slot"], repeated["start_slot"])
        self.assertFalse(repeated["rebuilt"])
        # one physical wash only
        self.assertEqual(self.controller.drain_count(), 1)

    def test_partial_failure_uses_last_good_value_and_flags_it(self) -> None:
        self.beds()
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.inputs.load_down.add("b1")
        result = self.board.rebuild(self.bank.bed_ids(), 5.0, "t2")
        self.assertTrue(result["rebuilt"])
        stale_beds = {item["bed_id"] for item in result["stale"]}
        self.assertIn("b1", stale_beds)
        b1 = next(job for job in self.board.state()["jobs"] if job["bed_id"] == "b1")
        self.assertTrue(b1["load_stale"])
        self.assertEqual(b1["load"], 9.0)  # last known good load kept
        self.assertTrue(self.board.state()["stale"])
        # recovery clears the stale flag and a repeat trigger stays idempotent
        self.inputs.load_down.remove("b1")
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t3")
        self.assertFalse(self.board.state()["stale"])
        self.assertFalse(self.board.rebuild(self.bank.bed_ids(), 5.0, "t3")["rebuilt"])

    def test_unreadable_bed_without_history_is_excluded(self) -> None:
        self.beds()
        self.inputs.load_down.add("b2")  # never successfully read before
        self.inputs.load_down.add("b1")
        result = self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        excluded = {item["bed_id"] for item in result["stale"] if item["excluded"]}
        self.assertIn("b1", excluded)  # b1 has last-good so it's not excluded
        self.assertIn("b2", excluded)  # no history at all
        planned = {job["bed_id"] for job in self.board.state()["jobs"]}
        self.assertNotIn("b2", planned)

    def test_plan_and_execution_ledger_stay_consistent(self) -> None:
        self.beds()
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.board.start("b1", "s1")
        running = self.board.reconcile()
        self.assertTrue(running["consistent"], running["mismatches"])
        self.assertEqual(running["drains"], ["b1"])
        self.board.finish("b1", "f1")
        self.assertFalse(self.bank.is_closed("b1"))
        self.assertTrue(self.board.reconcile()["consistent"])
        executions = self.board.state()["executions"]
        self.assertEqual(executions[-1]["finish_slot"], 100)

    def test_start_rolls_back_when_controller_rejects(self) -> None:
        self.beds()
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.bank.remove_bed("b1")  # controller.start will now fail
        before = self.board.state()["generation"]
        with self.assertRaises(ValueError):
            self.board.start("b1", "s1")
        self.assertEqual(self.board.state()["generation"], before)
        self.assertTrue(self.board.reconcile()["consistent"])

    def test_fcfs_policy_serves_first_arrival(self) -> None:
        self.bank.add_bed("b1", 1, 6.0)
        self.bank.add_bed("b2", 2, 9.0)
        board = ScheduleBoard(
            self.store,
            self.inputs,
            self.auditor,
            clock=self.clock,
            capacity=1,
            policy=Policy(overlap=FCFS),
        )
        board.rebuild(["b1"], 5.0, "t1")
        board.rebuild(["b1", "b2"], 5.0, "t2")  # b1 arrived first despite lower load
        waiting = [
            job["bed_id"]
            for job in board.state()["jobs"]
            if job["status"] == "waiting"
        ]
        self.assertEqual(waiting, ["b1", "b2"])
    def test_capacity_overload_blocks_start(self) -> None:
        self.bank.add_bed("b1", 1, 9.0)
        self.bank.add_bed("b2", 2, 9.0)
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.board.start("b1", "s1")
        self.board.start("b2", "s2")
        # third job with same slot window cannot start with only two crews
        self.bank.add_bed("b3", 3, 9.0)
        self.board.set_window("b3", 0, 0, 1)
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t2")
        with self.assertRaises(ValueError):
            self.board.start("b3", "s3")

    def test_missed_slot_is_replanned_on_later_rebuild(self) -> None:
        self.bank.add_bed("b1", 1, 9.0)
        self.bank.add_bed("b2", 2, 9.0)
        self.board.set_window("b1", 0, 10, 1)
        self.board.set_window("b2", 0, 10, 1)
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.board.start("b1", "s1")
        # two slots pass while b1 still runs; b2's planned slot 1 is now stale
        self.clock._time_fn.slot = 102
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t2")
        b2 = next(item for item in self.board.state()["assignments"] if item["bed_id"] == "b2")
        self.assertGreaterEqual(b2["start_slot"], 102)
        self.board.finish("b1", "f1")
        self.assertEqual(self.board.start("b2", "s2")["reason"], "started")

    def test_board_survives_reload_with_its_ledger(self) -> None:
        self.beds()
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.board.start("b1", "s1")
        reloaded = ScheduleBoard(
            self.store, self.inputs, self.auditor, clock=self.clock, capacity=2
        )
        reloaded.bind_controller(self.controller)
        state = reloaded.state()
        self.assertEqual([job["bed_id"] for job in state["running"]], ["b1"])
        self.assertTrue(any(item["kind"] == "start" for item in state["adjustments"]))
        self.assertTrue(state["reconcile"]["consistent"])

    def test_changes_are_audited(self) -> None:
        self.beds()
        self.board.rebuild(self.bank.bed_ids(), 5.0, "t1")
        self.board.rush("b2", "r1")
        kinds = self.auditor.count_by_kind()
        self.assertIn("schedule.adjust", kinds)
        self.assertIn("schedule.rush", kinds)
        state = self.board.state()
        self.assertTrue(any(item["kind"] == "rush" for item in state["adjustments"]))


if __name__ == "__main__":
    unittest.main()

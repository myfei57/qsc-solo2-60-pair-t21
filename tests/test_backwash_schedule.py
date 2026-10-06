"""Tests for constrained, audited backwash scheduling."""

from __future__ import annotations

import tempfile
import unittest

from waterplant.audit import Auditor
from waterplant.backwash import BackwashScheduler, Controller
from waterplant.filter import Bank
from waterplant.store import Store


class BackwashScheduleCase(unittest.TestCase):
    def _scheduler(self, beds: tuple[tuple[str, float], ...] = ()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store.open(f"{tmp.name}/state.json")
        bank = Bank()
        for index, (bed_id, load) in enumerate(beds, start=1):
            bank.add_bed(bed_id, index, load)
        auditor = Auditor(store)
        controller = Controller(bank, store)
        scheduler = BackwashScheduler(store, bank, controller, auditor)
        return scheduler, bank, controller, auditor, store

    def test_windows_and_crew_capacity_create_waiting_order(self) -> None:
        scheduler, bank, _, _, _ = self._scheduler((("a", 4.0), ("b", 9.0)))
        result = scheduler.trigger(
            "plan-1",
            {
                "a": {"window_start": 0, "window_end": 1},
                "b": {"window_start": 0, "window_end": 1},
            },
            current_slot=0,
            capacity=1,
        )
        self.assertEqual(result.slots[0].scheduled, ("b",))
        self.assertEqual([entry.bed_id for entry in result.slots[0].waiting], ["a"])
        self.assertEqual(result.slots[1].scheduled, ("a",))
        self.assertEqual(result.job("b").load, 9.0)

    def test_overlapping_window_uses_load_then_fcfs_tie_break(self) -> None:
        scheduler, _, _, _, _ = self._scheduler((("a", 5.0), ("b", 5.0)))
        result = scheduler.trigger(
            "plan-1",
            {
                "a": {"window_start": 0, "window_end": 2},
                "b": {"window_start": 0, "window_end": 2},
            },
            0,
            1,
        )
        self.assertEqual(result.slots[0].scheduled, ("a",))
        self.assertEqual([entry.bed_id for entry in result.slots[0].waiting], ["b"])
        self.assertEqual(result.slots[1].scheduled, ("b",))

    def test_urgent_insertion_does_not_move_running_work(self) -> None:
        scheduler, _, controller, auditor, _ = self._scheduler(
            (("a", 9.0), ("b", 5.0), ("c", 1.0))
        )
        scheduler.trigger(
            "plan-1",
            {bed: {"window_start": 0, "window_end": 3} for bed in ("a", "b", "c")},
            0,
            1,
        )
        scheduler.start("a", "start-a")
        before_audit = auditor.count()
        result = scheduler.expedite("c", "expedite-c")
        self.assertTrue(controller.is_running("a"))
        self.assertEqual(result.slots[0].running, ("a",))
        self.assertEqual(result.job("a").status, "running")
        self.assertEqual(result.job("a").scheduled_slot, 0)
        self.assertEqual(result.job("c").status, "scheduled")
        self.assertEqual(result.job("c").scheduled_slot, 1)
        self.assertEqual(result.job("b").status, "scheduled")
        self.assertEqual(result.job("b").scheduled_slot, 2)
        self.assertGreater(auditor.count(), before_audit)
        self.assertEqual(auditor.last().kind, "backwash_adjustment")

    def test_partial_failure_uses_last_valid_reading_and_marks_it(self) -> None:
        scheduler, bank, _, auditor, _ = self._scheduler((("a", 9.0),))
        scheduler.trigger("fresh", {"a": {"window_start": 0, "window_end": 2}}, 0, 1)
        bank.set_load("a", 20.0)
        result = scheduler.trigger(
            "stale",
            {"a": {"window_start": 0, "window_end": 2, "load_available": False}},
            0,
            1,
        )
        self.assertTrue(result.partial_failure)
        self.assertEqual(result.job("a").load, 9.0)
        self.assertEqual(result.job("a").load_status, "stale")
        self.assertIn("scheduled from last valid reading", result.job("a").reasons)
        self.assertTrue(any(entry.kind == "backwash_schedule" for entry in auditor.entries()))

    def test_missing_reading_without_last_value_blocks_work(self) -> None:
        scheduler, bank, _, _, _ = self._scheduler()
        bank.add_bed("unknown", 9, 5.0)
        # Simulate a demand source that has neither live data nor a cache.
        bank.remove_bed("unknown")
        result = scheduler.trigger(
            "missing",
            {"unknown": {"window_start": 0, "window_end": 2, "load_available": False}},
            0,
            1,
        )
        self.assertTrue(result.partial_failure)
        self.assertEqual(result.job("unknown").status, "blocked")
        self.assertEqual(result.job("unknown").load_status, "missing")
        self.assertIn("last valid data unavailable", result.job("unknown").reasons)

    def test_repeated_trigger_and_execution_calls_are_idempotent(self) -> None:
        scheduler, _, _, auditor, _ = self._scheduler((("a", 9.0), ("b", 5.0)))
        demands = {bed: {"window_start": 0, "window_end": 2} for bed in ("a", "b")}
        first = scheduler.trigger("same-plan", demands, 0, 1)
        second = scheduler.trigger("same-plan", demands, 0, 1)
        self.assertEqual(first.as_dict(), second.as_dict())
        started = scheduler.start("a", "start-a")
        repeated_start = scheduler.start("a", "start-a")
        self.assertEqual(started.as_dict(), repeated_start.as_dict())
        completed = scheduler.complete("a", "start-a", "complete-a", 1)
        repeated_complete = scheduler.complete("a", "start-a", "complete-a", 1)
        self.assertEqual(completed.as_dict(), repeated_complete.as_dict())
        self.assertEqual(auditor.count_by_kind().get("backwash_schedule"), 1)
        self.assertEqual(auditor.count_by_kind().get("backwash_start"), 1)
        self.assertEqual(auditor.count_by_kind().get("backwash_complete"), 1)

    def test_completion_retry_after_physical_delete_does_not_duplicate_audit(self) -> None:
        scheduler, _, controller, auditor, _ = self._scheduler((("a", 9.0),))
        scheduler.trigger("plan", {"a": {"window_start": 0, "window_end": 2}}, 0, 1)
        scheduler.start("a", "start-a")
        controller.complete("a")
        before = auditor.count_by_kind().get("backwash_complete", 0)
        result = scheduler.complete("a", "start-a", "complete-a", 1)
        self.assertEqual(result.job("a"), None)
        self.assertFalse(controller.is_running("a"))
        self.assertEqual(auditor.count_by_kind().get("backwash_complete", 0), before)

    def test_reconcile_returns_schedule_marker_to_queue_when_execution_disappears(self) -> None:
        scheduler, _, controller, _, store = self._scheduler((("a", 9.0),))
        scheduler.trigger("plan", {"a": {"window_start": 0, "window_end": 2}}, 0, 1)
        scheduler.start("a", "start-a")
        store.delete("backwash:drain:a")
        result = scheduler.reconcile()
        self.assertFalse(controller.is_running("a"))
        self.assertEqual(result.job("a").status, "scheduled")
        self.assertIn("missing execution record; returned to queue", result.job("a").reasons)


if __name__ == "__main__":
    unittest.main()

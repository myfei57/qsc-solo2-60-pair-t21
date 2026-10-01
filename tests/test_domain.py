"""Behavioural tests for the control components."""

from __future__ import annotations

import tempfile
import unittest

from waterplant.audit import Auditor
from waterplant.backwash import Controller
from waterplant.chlor import Doser as ChlorDoser
from waterplant.chlor import validate_demand
from waterplant.clearwell import Well, validate_level
from waterplant.coag import Doser as CoagDoser
from waterplant.filter import Bank, validate_zone
from waterplant.flow import Calibration, calibrate_meter, validate_factor
from waterplant.intake import FlowRepository, InletController, Sensor, mix, validate_flow
from waterplant.intake import Trend
from waterplant.inventory import Inventory, Lot, validate_quantity
from waterplant.ns import Stage, treatment_line
from waterplant.ph import Stabilizer, validate_ph
from waterplant.quota import Accumulator, Quota, check_quota, validate_amount
from waterplant.reporting import audit_export, to_csv
from waterplant.scheduler import Scheduler, validate_threshold
from waterplant.store import (
    Store,
    append_command,
    append_event,
    advance_epoch,
    clear_commands,
    event_count,
    list_events,
    load_commands,
)
from waterplant.turb import Sampler, verdict_for


class StoreCase(unittest.TestCase):
    def test_round_trip_persists_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            store.put("alpha", "1")
            store.put("beta", "2")
            self.assertEqual(store.keys(), ["alpha", "beta"])
            self.assertEqual(store.count(), 2)
            self.assertEqual(store.get("alpha"), ("1", True))
            self.assertEqual(store.get("missing"), ("", False))
            store.delete("alpha")
            self.assertEqual(store.keys(), ["beta"])
            reopened = Store.open(f"{tmp}/state.json")
            self.assertEqual(reopened.get("beta"), ("2", True))
            store.clear()
            self.assertEqual(store.count(), 0)

    def test_command_lists_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            self.assertEqual(load_commands(store, "list"), [])
            append_command(store, "list", "one")
            append_command(store, "list", "two")
            self.assertEqual(load_commands(store, "list"), ["one", "two"])
            clear_commands(store, "list")
            self.assertEqual(load_commands(store, "list"), [])

    def test_epoch_wraps_and_stays_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            self.assertEqual(advance_epoch(store, "counter", 40.0, 100.0), 40.0)
            self.assertEqual(advance_epoch(store, "counter", 90.0, 100.0), 30.0)
            self.assertGreaterEqual(advance_epoch(store, "counter", 95.0, 100.0), 0.0)

    def test_history_records_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            append_event(store, "events", "seed", "defaults")
            append_event(store, "events", "manual", "operator")
            events = list_events(store, "events")
            self.assertEqual([event.kind for event in events], ["seed", "manual"])
            self.assertEqual(event_count(store, "events"), 2)
            self.assertGreater(events[0].at, 0)


class PipelineCase(unittest.TestCase):
    def test_treatment_line_is_ordered(self) -> None:
        line = treatment_line()
        self.assertEqual(line.count(), 7)
        self.assertTrue(line.contains(Stage.FILTER))
        self.assertTrue(line.before(Stage.INTAKE, Stage.COAG))
        self.assertFalse(line.before(Stage.AUDIT, Stage.COAG))
        self.assertEqual(line.last(), Stage.AUDIT)
        self.assertEqual(len(line.steps()), 7)
        self.assertEqual(line.steps()[0].action, "collect flow")
        self.assertIn("treatment", line.describe())


class IntakeCase(unittest.TestCase):
    def test_sensor_payload_defaults(self) -> None:
        sensor = Sensor.from_payload({"flow": 12})
        self.assertEqual(sensor.flow, 12.0)
        self.assertEqual(sensor.turbidity, 0.0)
        self.assertEqual(sensor.as_dict(), {"flow": 12.0, "turbidity": 0.0})

    def test_repository_records_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            repository = FlowRepository(store)
            self.assertEqual(repository.load_flow(), (0.0, False))
            repository.record(Sensor(flow=9.5, turbidity=2.5))
            self.assertEqual(repository.load_flow(), (9.5, True))
            self.assertEqual(repository.turbidity(), 2.5)
            self.assertTrue(repository.state().present)
            self.assertIn("flow=9.5000", repository.describe())

    def test_validation_and_mixing(self) -> None:
        validate_flow(0.0)
        with self.assertRaises(ValueError):
            validate_flow(-1.0)
        with self.assertRaises(ValueError):
            validate_flow(2_000_000.0)
        self.assertEqual(mix([]), 0.0)
        self.assertEqual(mix([2.0, 4.0]), 3.0)

    def test_inlet_controller_never_goes_negative(self) -> None:
        controller = InletController()
        self.assertEqual(controller.raise_level(1.0, 2.0), 3.0)
        self.assertEqual(controller.lower_level(1.0, 5.0), 0.0)


class DosingCase(unittest.TestCase):
    def test_coagulant_dose_follows_persisted_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            repository = FlowRepository(store)
            repository.persist_flow(10.0)
            doser = CoagDoser(store)
            self.assertEqual(doser.update_flow_and_dose(20.0), 20.0)
            self.assertEqual(doser.state().persisted_flow, 20.0)
            self.assertIn("coagulant", doser.describe())

    def test_coagulant_ratio_tracks_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            doser = CoagDoser(store)
            self.assertEqual(doser.current_ratio(), 1.0)
            Calibration(store).replace(2.0)
            self.assertEqual(doser.current_ratio(), 2.0)
            self.assertEqual(doser.dose_for_flow(3.0), 6.0)
            self.assertEqual(doser.dose_plan(1.0, 1.0), 2.0)

    def test_chlorine_dose_follows_fresh_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            doser = ChlorDoser(store)
            self.assertEqual(doser.current_target(), 0.5)
            Well(store).update_residual_demand(1.0)
            self.assertEqual(doser.current_target(), 1.0)
            self.assertEqual(doser.apply_residual(), 1.0)
            self.assertEqual(doser.target_for_demand(0.0), 0.3)
            self.assertIn("chlorine", doser.describe())

    def test_demand_validation(self) -> None:
        validate_demand(0.0)
        with self.assertRaises(ValueError):
            validate_demand(-0.1)
        with self.assertRaises(ValueError):
            validate_demand(2000.0)


class TurbidityCase(unittest.TestCase):
    def test_verdict_and_sampling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            sampler = Sampler(CoagDoser(store))
            self.assertEqual(sampler.judge([0.2, 0.4]), 0.0)
            self.assertEqual(sampler.state().last_verdict, 0.0)
            self.assertEqual(sampler.judge([4.0, 6.0]), 5.0)
            self.assertEqual(verdict_for(0.5), 0.0)
            self.assertIn("turbidity", sampler.describe())


class CalibrationCase(unittest.TestCase):
    def test_meter_replacement_updates_factor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            calibration = Calibration(store)
            self.assertEqual(calibration.current(), 1.0)
            meter = calibrate_meter("fm-2", 1.5)
            calibration.replace(meter.factor)
            self.assertEqual(calibration.current(), 1.5)
            self.assertEqual(calibration.state().factor, 1.5)
            self.assertEqual(meter.as_dict(), {"serial": "fm-2", "factor": 1.5})
            self.assertIn("factor=1.5000", calibration.describe())

    def test_factor_validation(self) -> None:
        validate_factor(1.0)
        with self.assertRaises(ValueError):
            validate_factor(0.0)
        with self.assertRaises(ValueError):
            validate_factor(500.0)


class ClearwellCase(unittest.TestCase):
    def test_adjustment_raises_before_lowering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            well = Well(store)
            well.set_level(10.0)
            minimum = well.adjust_level(14.0, InletController(), InletController())
            self.assertEqual(minimum, 10.0)
            self.assertEqual(well.level(), 14.0)
            self.assertEqual(well.adjust_level(6.0, InletController(), InletController()), 6.0)
            self.assertEqual(well.state().level, 6.0)
            self.assertIn("clearwell", well.describe())

    def test_level_validation(self) -> None:
        validate_level(0.0)
        with self.assertRaises(ValueError):
            validate_level(-0.5)
        with self.assertRaises(ValueError):
            validate_level(2_000_000.0)


class FilterCase(unittest.TestCase):
    def test_bank_lifecycle_and_rotation(self) -> None:
        bank = Bank()
        bank.add_bed("b1", 1, 5.0)
        bank.add_bed("b2", 2, 9.0)
        bank.add_bed("b3", 3, 1.0)
        self.assertEqual(bank.count(), 3)
        self.assertEqual(bank.bed_ids(), ["b1", "b2", "b3"])
        self.assertEqual(bank.dirtiest(), "b2")
        self.assertEqual(bank.cleanest(), "b3")
        self.assertEqual(bank.total_load(), 15.0)
        bank.close("b1")
        self.assertTrue(bank.is_closed("b1"))
        self.assertEqual(bank.active_count(), 2)
        bank.open("b1")
        self.assertFalse(bank.is_closed("b1"))
        bank.rotate("b2")
        self.assertEqual(bank.on_duty(), "b2")
        bank.reset_closed()
        self.assertEqual(bank.active_count(), 3)
        bank.renumber("b3", 9)
        self.assertEqual(bank.mapping()[9], "b3")
        self.assertEqual(bank.bed_zone("b3"), (9, True))
        self.assertIn("beds=3", bank.describe())
        bank.remove_bed("b1")
        self.assertEqual(bank.count(), 2)

    def test_unknown_bed_raises(self) -> None:
        bank = Bank()
        with self.assertRaises(ValueError):
            bank.close("missing")
        with self.assertRaises(ValueError):
            bank.remove_bed("missing")
        with self.assertRaises(ValueError):
            bank.rotate("missing")
        self.assertEqual(bank.bed("missing"), None)
        self.assertEqual(bank.on_duty(), None)
        self.assertEqual(bank.dirtiest(), "")

    def test_zone_validation(self) -> None:
        validate_zone(1)
        with self.assertRaises(ValueError):
            validate_zone(-1)
        with self.assertRaises(ValueError):
            validate_zone(200_000)


class BackwashCase(unittest.TestCase):
    def test_start_closes_before_draining(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            bank = Bank()
            bank.add_bed("b1", 1, 4.0)
            controller = Controller(bank, store)
            controller.start("b1")
            self.assertTrue(bank.is_closed("b1"))
            self.assertFalse(controller.spilled())
            self.assertEqual(controller.drain_count(), 1)
            self.assertEqual(controller.last_drain(), "b1")
            self.assertIn("commands=0", controller.describe())

    def test_queue_replay_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            bank = Bank()
            bank.add_bed("b1", 1, 4.0)
            bank.add_bed("b2", 2, 2.0)
            controller = Controller(bank, store)
            controller.enqueue("b1")
            controller.enqueue("b2")
            self.assertEqual(controller.pending_count(), 2)
            self.assertEqual(controller.replay(), ["b1", "b2"])
            self.assertTrue(bank.is_closed("b1"))
            controller.recover()
            self.assertEqual(controller.command_list(), [])

    def test_rotation_and_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            bank = Bank()
            bank.add_bed("b1", 1, 4.0)
            bank.add_bed("b2", 2, 9.0)
            controller = Controller(bank, store)
            self.assertEqual(controller.order_rotation(), ["b2", "b1"])
            self.assertEqual(bank.on_duty(), "b2")
            self.assertEqual(controller.select(1), "b1")
            self.assertEqual(controller.eligible(), ["b1", "b2"])
            with self.assertRaises(ValueError):
                controller.select(7)


class QuotaCase(unittest.TestCase):
    def test_accumulator_and_remaining(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            accumulator = Accumulator(store)
            self.assertEqual(accumulator.add(10.0), 10.0)
            self.assertEqual(accumulator.value(), 10.0)
            self.assertEqual(accumulator.remaining(30.0), 20.0)
            self.assertEqual(accumulator.remaining(5.0), 0.0)
            accumulator.reset()
            self.assertEqual(accumulator.value(), 0.0)
            self.assertIn("quota accumulator", accumulator.describe())

    def test_check_quota_and_validation(self) -> None:
        quota = Quota(chemical="chlorine", limit=100.0)
        self.assertEqual(check_quota(quota.chemical, 40.0, quota.limit), (60.0, True))
        self.assertEqual(check_quota(quota.chemical, 120.0, quota.limit), (-20.0, False))
        self.assertEqual(check_quota(quota.chemical, 5.0, 0.0), (5.0, True))
        validate_amount(1.0)
        with self.assertRaises(ValueError):
            validate_amount(-1.0)


class AuditCase(unittest.TestCase):
    def test_entries_and_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            auditor = Auditor(store)
            self.assertEqual(auditor.count(), 0)
            self.assertEqual(auditor.last(), None)
            auditor.record("coagulant", "1.0000")
            auditor.record("chlorine", "0.5000")
            auditor.record("coagulant", "2.0000")
            self.assertEqual(auditor.count(), 3)
            self.assertEqual(auditor.count_by_kind(), {"coagulant": 2, "chlorine": 1})
            self.assertEqual(len(auditor.filter("coagulant")), 2)
            self.assertEqual(auditor.kinds(), ["coagulant", "chlorine"])
            self.assertEqual(auditor.last().kind, "coagulant")
            self.assertEqual(auditor.state().count, 3)
            self.assertEqual(auditor.totals(), {"coagulant": 3.0, "chlorine": 0.5})
            self.assertIn("audit entries=3", auditor.describe())


class PhCase(unittest.TestCase):
    def test_band_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            stabilizer = Stabilizer(store)
            self.assertEqual(stabilizer.current(), 7.0)
            self.assertTrue(stabilizer.is_stable())
            self.assertEqual(stabilizer.adjustment(), 0.0)
            self.assertEqual(stabilizer.stabilize().direction, "hold")

            verdict = stabilizer.read(6.0)
            self.assertFalse(verdict.stable)
            self.assertEqual(verdict.direction, "raise")
            self.assertAlmostEqual(verdict.adjustment, 0.5)

            verdict = stabilizer.read(9.0)
            self.assertEqual(verdict.direction, "lower")
            self.assertAlmostEqual(verdict.adjustment, -0.5)
            self.assertIn("ph value", stabilizer.describe())
            self.assertFalse(stabilizer.state().stable)

    def test_ph_validation(self) -> None:
        validate_ph(7.0)
        with self.assertRaises(ValueError):
            validate_ph(-0.1)
        with self.assertRaises(ValueError):
            validate_ph(20.0)


class SchedulerCase(unittest.TestCase):
    def test_plan_and_due_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            bank = Bank()
            bank.add_bed("b1", 1, 2.0)
            bank.add_bed("b2", 2, 9.0)
            bank.add_bed("b3", 3, 6.0)
            scheduler = Scheduler(store)
            self.assertEqual(scheduler.threshold(), 6.0)
            self.assertEqual(scheduler.due(bank), ["b2", "b3"])
            plan = scheduler.plan(bank)
            self.assertEqual([entry.bed_id for entry in plan], ["b2", "b3", "b1"])
            self.assertEqual([entry.priority for entry in plan], [1, 2, 3])
            self.assertEqual(plan[0].drain_seconds, 75)
            scheduler.set_threshold(3.0)
            self.assertEqual(scheduler.due(bank), ["b2", "b3"])
            self.assertEqual(scheduler.state(bank).threshold, 3.0)
            self.assertIn("schedule threshold", scheduler.describe(bank))

    def test_threshold_validation(self) -> None:
        validate_threshold(1.0)
        with self.assertRaises(ValueError):
            validate_threshold(0.0)
        with self.assertRaises(ValueError):
            validate_threshold(2_000_000.0)


class TrendCase(unittest.TestCase):
    def test_window_and_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            trend = Trend(store)
            self.assertEqual(trend.window(), 12)
            self.assertEqual(trend.stats().samples, 0)
            trend.record(10.0)
            trend.record(20.0)
            stats = trend.record(30.0)
            self.assertEqual(stats.samples, 3)
            self.assertEqual(stats.mean, 20.0)
            self.assertEqual(stats.minimum, 10.0)
            self.assertEqual(stats.maximum, 30.0)
            trend.set_window(2)
            self.assertEqual(trend.values(), [20.0, 30.0])
            self.assertIn("intake trend", trend.describe())
            trend.reset()
            self.assertEqual(trend.values(), [])

    def test_window_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trend = Trend(Store.open(f"{tmp}/state.json"))
            with self.assertRaises(ValueError):
                trend.set_window(0)
            with self.assertRaises(ValueError):
                trend.set_window(5000)


class InventoryCase(unittest.TestCase):
    def test_receive_consume_and_reorder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            inventory = Inventory(store)
            self.assertEqual(inventory.balance(), 0.0)
            self.assertEqual(inventory.reorder_level(), 25.0)
            self.assertEqual(inventory.chemicals(), [])

            inventory.receive("chlorine", "L1", 40.0)
            inventory.receive("chlorine", "L2", 30.0)
            inventory.receive("coagulant", "C1", 10.0)
            self.assertEqual(inventory.balance(), 80.0)
            self.assertEqual(inventory.balance("chlorine"), 70.0)
            self.assertEqual(inventory.chemicals(), ["chlorine", "coagulant"])

            inventory.consume("chlorine", 50.0)
            lots = inventory.lots("chlorine")
            self.assertEqual([lot.consumed for lot in lots], [40.0, 10.0])
            self.assertEqual(inventory.balance("chlorine"), 20.0)
            self.assertEqual(inventory.needs_reorder(), ["chlorine", "coagulant"])

            inventory.set_reorder_level(5.0)
            self.assertEqual(inventory.needs_reorder(), [])
            state = inventory.state()
            self.assertEqual(state.reorder_level, 5.0)
            self.assertEqual(len(state.lots), 3)
            self.assertIn("inventory balance", inventory.describe())

    def test_insufficient_stock_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Inventory(Store.open(f"{tmp}/state.json"))
            inventory.receive("coagulant", "C1", 5.0)
            with self.assertRaises(ValueError):
                inventory.consume("coagulant", 6.0)
            with self.assertRaises(ValueError):
                inventory.consume("missing", 1.0)
            self.assertEqual(Lot("x", "y", 3.0, 1.0).remaining(), 2.0)
            validate_quantity(0.0)
            with self.assertRaises(ValueError):
                validate_quantity(-1.0, "level")


class ReportingCase(unittest.TestCase):
    def test_csv_rendering_escapes_fields(self) -> None:
        text = to_csv(["a", "b"], [[1, "plain"], [2, 'has,comma'], [3, 'has"quote']])
        self.assertTrue(text.startswith("a,b\n"))
        self.assertIn('2,"has,comma"', text)
        self.assertIn('3,"has""quote"', text)

    def test_audit_export_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auditor = Auditor(Store.open(f"{tmp}/state.json"))
            auditor.record("coagulant", "2.0000")
            columns, rows = audit_export(auditor)
            self.assertEqual(columns, ["time", "kind", "detail", "id"])
            self.assertEqual(rows[0][1], "coagulant")
            self.assertEqual(rows[0][2], "2.0000")


if __name__ == "__main__":
    unittest.main()

"""End to end tests for the HTTP control console."""

from __future__ import annotations

import json
import tempfile
import unittest

from waterplant.console.routes import route_table
from waterplant.console.seed import seed_defaults
from waterplant.console.server import Server
from waterplant.store import Store


class ConsoleCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store.open(f"{self._tmp.name}/state.json")
        seed_defaults(self.store)
        self.server = Server(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def call(self, method: str, path: str, payload: dict | None = None):
        response = self.server.dispatch(method, path, payload)
        body = response.body.decode("utf-8")
        parsed = json.loads(body) if response.content_type.startswith("application/json") else body
        return response.status, parsed

    def test_health_snapshot_and_reports(self) -> None:
        status, health = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertIn("intake:flow", health["keys"])

        status, checks = self.call("GET", "/health/checks")
        self.assertEqual(status, 200)
        self.assertEqual({check["name"] for check in checks["checks"]}, {
            "store",
            "calibration",
            "residual",
            "quota",
            "intake",
            "ph",
            "inventory",
        })

        status, snapshot = self.call("GET", "/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(
            set(snapshot),
            {
                "pipeline",
                "store",
                "intake",
                "coag",
                "chlor",
                "filter",
                "backwash",
                "turbidity",
                "flow",
                "clearwell",
                "quota",
                "audit",
                "ph",
                "schedule",
                "trend",
                "inventory",
            },
        )

        status, describe = self.call("GET", "/describe")
        self.assertEqual(status, 200)
        self.assertIn("pipeline", describe)

        status, report = self.call("GET", "/report")
        self.assertEqual(status, 200)
        self.assertIn("waterplant control report", report)

    def test_metadata_routes(self) -> None:
        status, pipeline = self.call("GET", "/pipeline")
        self.assertEqual(status, 200)
        self.assertTrue(pipeline["ordered"])
        self.assertEqual(pipeline["last_stage"], "audit")

        status, catalog = self.call("GET", "/catalog")
        self.assertEqual(status, 200)
        self.assertEqual(len(catalog["routes"]), len(route_table()))

        status, ops = self.call("GET", "/ops")
        self.assertEqual(status, 200)
        self.assertTrue(ops["flow_present"])

        status, telemetry = self.call("GET", "/telemetry")
        self.assertEqual(status, 200)
        self.assertEqual(telemetry["filter_beds"], 0)

        status, version = self.call("GET", "/version")
        self.assertEqual(status, 200)
        self.assertEqual(version["name"], "waterplant-control")

        status, system = self.call("GET", "/system")
        self.assertEqual(status, 200)
        self.assertIn("python", system)

    def test_history_round_trip(self) -> None:
        status, _ = self.call("POST", "/history", {"kind": "manual", "value": "start"})
        self.assertEqual(status, 200)
        status, listing = self.call("GET", "/history")
        self.assertEqual(status, 200)
        self.assertTrue(listing["has_events"])
        self.assertEqual(listing["count"], 2)
        status, _ = self.call("POST", "/history/clear")
        self.assertEqual(status, 200)
        _, listing = self.call("GET", "/history")
        self.assertEqual(listing["count"], 0)
        status, error = self.call("POST", "/history", {})
        self.assertEqual(status, 400)
        self.assertIn("kind", error["error"])

    def test_intake_and_dosing_routes(self) -> None:
        status, body = self.call("POST", "/intake/flow", {"flow": 1200, "turbidity": 3.0})
        self.assertEqual(status, 200)
        self.assertEqual(body["flow"], 1200.0)
        status, body = self.call("GET", "/intake/flow")
        self.assertEqual(body["flow"], 1200.0)

        status, body = self.call("POST", "/coag/dose", {"flow": 1500})
        self.assertEqual(status, 200)
        self.assertEqual(body["dose"], 1500.0)
        status, body = self.call("POST", "/coag/turbidity", {"samples": [4.0, 6.0]})
        self.assertEqual(body["dose"], 5.0)
        status, body = self.call("GET", "/coag/ratio")
        self.assertEqual(body["ratio"], 1.0)

        status, body = self.call("POST", "/flow/replace", {"serial": "fm-7", "factor": 2.0})
        self.assertEqual(body["meter"]["factor"], 2.0)
        _, body = self.call("GET", "/coag/ratio")
        self.assertEqual(body["ratio"], 2.0)

        status, body = self.call("POST", "/chlor/target", {"demand": 1.0})
        self.assertEqual(body["target"], 1.0)
        status, body = self.call("POST", "/chlor/dose", {})
        self.assertEqual(body["dose"], 1.0)

        status, body = self.call("POST", "/clearwell/level", {"target": 12.0})
        self.assertEqual(body["level"], 12.0)
        self.assertEqual(body["min_level"], 0.0)

    def test_filter_and_backwash_routes(self) -> None:
        for bed_id, zone, load in (("b1", 1, 4.0), ("b2", 2, 9.0), ("b3", 3, 1.0)):
            status, _ = self.call(
                "POST", "/filter/add", {"id": bed_id, "zone": zone, "load": load}
            )
            self.assertEqual(status, 200)

        status, body = self.call("POST", "/filter/close", {"id": "b1"})
        self.assertEqual(body["closed"], "b1")
        status, body = self.call("GET", "/backwash/order")
        self.assertEqual(body["order"], ["b2", "b1", "b3"])
        self.assertEqual(body["on_duty"], "b2")

        status, body = self.call("POST", "/backwash/select", {"zone": 3})
        self.assertEqual(body["bed"], "b3")
        status, body = self.call("POST", "/filter/zone", {"id": "b3"})
        self.assertTrue(body["ok"])

        status, body = self.call("POST", "/filter/renumber", {"id": "b3", "zone": 7})
        self.assertEqual(body["zone"], 7)
        _, body = self.call("POST", "/backwash/select", {"zone": 7})
        self.assertEqual(body["bed"], "b3")

        status, _ = self.call("POST", "/backwash/enqueue", {"id": "b1"})
        self.assertEqual(status, 200)
        _, body = self.call("POST", "/backwash/replay")
        self.assertEqual(body["replayed"], ["b1"])
        status, _ = self.call("POST", "/backwash/recover")
        self.assertEqual(status, 200)

        status, body = self.call("POST", "/filter/load", {"id": "b1", "load": 20.0})
        self.assertEqual(body["load"], 20.0)
        status, _ = self.call("POST", "/filter/reset")
        self.assertEqual(status, 200)
        status, _ = self.call("POST", "/filter/open", {"id": "b1"})
        self.assertEqual(status, 200)
        status, body = self.call("POST", "/filter/remove", {"id": "b3"})
        self.assertEqual(body["removed"], "b3")

    def test_quota_and_audit_routes(self) -> None:
        status, body = self.call("POST", "/quota/add", {"amount": 40.0})
        self.assertEqual(body["value"], 40.0)
        status, body = self.call("GET", "/quota")
        self.assertEqual(body["value"], 40.0)
        status, body = self.call("POST", "/quota/check", {"chemical": "chlorine", "used": 40.0, "limit": 100.0})
        self.assertEqual(body["remaining"], 60.0)
        self.assertTrue(body["ok"])

        self.call("POST", "/coag/dose", {"flow": 5.0})
        self.call("POST", "/chlor/dose", {})
        status, body = self.call("GET", "/audit")
        self.assertEqual(len(body["entries"]), 2)
        status, body = self.call("GET", "/audit/summary")
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["by_kind"], {"coagulant": 1, "chlorine": 1})
        status, body = self.call("POST", "/audit/filter", {"kind": "chlorine"})
        self.assertEqual(len(body["entries"]), 1)

    def test_cycle_and_simulation(self) -> None:
        self.call("POST", "/filter/add", {"id": "b1", "zone": 1, "load": 3.0})
        status, body = self.call(
            "POST",
            "/cycle",
            {
                "flow": 800.0,
                "samples": [2.0, 4.0],
                "demand": 0.5,
                "level": 10.0,
                "bed_id": "b1",
                "zone": 1,
                "amount": 12.0,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["coag_dose"], 800.0)
        self.assertEqual(body["turb_dose"], 3.0)
        self.assertEqual(body["chlor_dose"], 0.65)
        self.assertEqual(body["rotation"], ["b1"])
        self.assertEqual(body["drains"], 1)
        self.assertEqual(body["audit_count"], 2)
        self.assertTrue(body["ph_stable"])
        self.assertEqual(body["ph_adjustment"], 0.0)

        status, body = self.call("POST", "/simulate", {"ticks": 3})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["ticks"]), 3)
        self.assertEqual(body["last_stage"], "audit")
        status, body = self.call("POST", "/simulate", {})
        self.assertEqual(len(body["ticks"]), 5)

    def test_ph_and_schedule_routes(self) -> None:
        status, body = self.call("GET", "/ph")
        self.assertEqual(status, 200)
        self.assertTrue(body["stable"])
        self.assertEqual(body["band_low"], 6.5)

        status, body = self.call("POST", "/ph/read", {"value": 5.0})
        self.assertEqual(status, 200)
        self.assertFalse(body["stable"])
        self.assertEqual(body["direction"], "raise")
        self.assertAlmostEqual(body["adjustment"], 1.5)

        status, body = self.call("GET", "/ph/verdict")
        self.assertEqual(body["direction"], "raise")
        status, body = self.call("POST", "/ph/read", {"value": 9.5})
        self.assertEqual(body["direction"], "lower")

        status, body = self.call("GET", "/schedule")
        self.assertEqual(status, 200)
        self.assertEqual(body["threshold"], 6.0)
        self.assertEqual(body["due"], [])

        for bed_id, load in (("b1", 2.0), ("b2", 9.0)):
            self.call("POST", "/filter/add", {"id": bed_id, "zone": 1, "load": load})
        status, body = self.call("GET", "/schedule")
        self.assertEqual(body["due"], ["b2"])
        status, body = self.call("POST", "/schedule/plan", {})
        self.assertEqual([entry["bed_id"] for entry in body["entries"]], ["b2", "b1"])
        self.assertEqual(body["entries"][0]["priority"], 1)
        self.assertEqual(body["entries"][0]["drain_seconds"], 75)
        status, body = self.call("POST", "/schedule/threshold", {"threshold": 2.5})
        self.assertEqual(body["threshold"], 2.5)
        _, body = self.call("GET", "/schedule")
        self.assertEqual(body["due"], ["b2"])

    def test_trend_and_audit_totals_routes(self) -> None:
        for flow in (100.0, 200.0, 300.0):
            self.call("POST", "/intake/flow", {"flow": flow})
        status, body = self.call("GET", "/intake/trend")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["samples"], 3)
        self.assertGreater(body["maximum"], body["minimum"])
        status, body = self.call("POST", "/intake/trend/window", {"window": 2})
        self.assertEqual(body["window"], 2)
        _, body = self.call("GET", "/intake/trend")
        self.assertEqual(body["samples"], 2)
        status, _ = self.call("POST", "/intake/trend/reset")
        self.assertEqual(status, 200)
        _, body = self.call("GET", "/intake/trend")
        self.assertEqual(body["samples"], 0)

        self.call("POST", "/coag/dose", {"flow": 5.0})
        self.call("POST", "/coag/dose", {"flow": 7.0})
        self.call("POST", "/chlor/dose", {})
        status, body = self.call("GET", "/audit/totals")
        self.assertEqual(status, 200)
        self.assertEqual(body["totals"]["coagulant"], 12.0)
        self.assertEqual(body["totals"]["chlorine"], 0.5)

    def test_inventory_and_metrics_routes(self) -> None:
        status, body = self.call("POST", "/inventory/receive", {"chemical": "chlorine", "lot_id": "L1", "amount": 40.0})
        self.assertEqual(status, 200)
        self.assertEqual(body["lot"]["remaining"], 40.0)
        self.call("POST", "/inventory/receive", {"chemical": "chlorine", "lot_id": "L2", "amount": 30.0})

        status, body = self.call("GET", "/inventory")
        self.assertEqual(status, 200)
        self.assertEqual(body["balance"], 70.0)
        self.assertEqual(body["needs_reorder"], [])

        status, body = self.call("POST", "/inventory/consume", {"chemical": "chlorine", "amount": 50.0})
        self.assertEqual(body["balance"], 20.0)
        _, body = self.call("GET", "/inventory")
        self.assertEqual(body["needs_reorder"], ["chlorine"])
        self.assertEqual(body["lots"][0]["consumed"], 40.0)
        self.assertEqual(body["lots"][1]["consumed"], 10.0)

        status, body = self.call("POST", "/inventory/reorder-level", {"level": 10.0})
        self.assertEqual(body["reorder_level"], 10.0)
        _, checks = self.call("GET", "/health/checks")
        inventory_check = [c for c in checks["checks"] if c["name"] == "inventory"][0]
        self.assertEqual(inventory_check["status"], "ok")

        status, body = self.call("POST", "/inventory/consume", {"chemical": "chlorine", "amount": 100.0})
        self.assertEqual(status, 400)
        self.assertIn("insufficient", body["error"])

        status, body = self.call("GET", "/metrics")
        self.assertGreater(body["requests"], 0)
        self.assertIn("GET /inventory", body["by_route"])
        status, body = self.call("POST", "/metrics/reset", {})
        self.assertTrue(body["reset"])
        _, body = self.call("GET", "/metrics")
        self.assertEqual(body["requests"], 1)

    def test_export_routes(self) -> None:
        self.call("POST", "/coag/dose", {"flow": 5.0})
        status, body = self.call("GET", "/export/audit")
        self.assertEqual(status, 200)
        self.assertIn("time,kind,detail,id", body)
        self.assertIn("coagulant", body)

        status, body = self.call("GET", "/export/telemetry")
        self.assertEqual(status, 200)
        self.assertIn("metric,value", body)
        self.assertIn("filter_beds", body)

    def test_reset_store_and_errors(self) -> None:
        status, body = self.call("POST", "/ops/reset-store", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["reset"])
        self.assertEqual(body["keys"], 0)

        status, body = self.call("GET", "/missing")
        self.assertEqual(status, 404)
        self.assertIn("no route", body["error"])

        status, body = self.call("DELETE", "/health")
        self.assertEqual(status, 405)
        self.assertIn("not allowed", body["error"])

        status, body = self.call("POST", "/coag/dose", {"flow": -5})
        self.assertEqual(status, 400)
        self.assertIn("non-negative", body["error"])

        status, body = self.call("POST", "/coag/dose", {"flow": "abc"})
        self.assertEqual(status, 400)
        self.assertIn("must be a number", body["error"])

        status, body = self.call("POST", "/filter/close", {"id": "ghost"})
        self.assertEqual(status, 400)
        self.assertIn("not found", body["error"])


class WsgiCase(unittest.TestCase):
    def test_wsgi_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store.open(f"{tmp}/state.json")
            seed_defaults(store)
            server = Server(store)
            captured: dict[str, object] = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            payload = json.dumps({"flow": 5.0}).encode("utf-8")

            class Stream:
                def read(self, size: int) -> bytes:
                    return payload

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/coag/dose",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(payload)),
                "wsgi.input": Stream(),
            }
            body = b"".join(server(environ, start_response))
            self.assertEqual(captured["status"], "200 OK")
            self.assertEqual(json.loads(body)["dose"], 5.0)
            store.close()


if __name__ == "__main__":
    unittest.main()

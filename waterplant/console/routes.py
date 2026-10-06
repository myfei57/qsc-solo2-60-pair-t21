"""Single source of truth for the console route table."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from .http import Router


@dataclass(frozen=True)
class Route:
    """One console endpoint and the handler attribute that serves it."""

    method: str
    path: str
    note: str
    handler: str

    def as_dict(self) -> dict[str, str]:
        return {"method": self.method, "path": self.path, "note": self.note}


def route_table() -> list[Route]:
    """Every route the console serves, in catalog order."""

    return [
        Route("GET", "/health", "service health and persisted keys", "health"),
        Route("GET", "/health/checks", "detailed component health checks", "health_checks"),
        Route("GET", "/snapshot", "full component snapshot", "snapshot"),
        Route("GET", "/describe", "human readable component summary", "describe"),
        Route("GET", "/report", "plain text control report", "report"),
        Route("GET", "/pipeline", "treatment pipeline stages", "pipeline"),
        Route("GET", "/catalog", "list all HTTP routes", "catalog"),
        Route("GET", "/ops", "operator counters", "ops_counters"),
        Route("POST", "/ops/reset-store", "factory reset persisted store", "ops_reset_store"),
        Route("GET", "/telemetry", "runtime telemetry counters", "telemetry"),
        Route("GET", "/version", "build version information", "version"),
        Route("GET", "/system", "runtime system information", "system"),
        Route("POST", "/history", "append a console event", "history_append"),
        Route("GET", "/history", "list console events", "history_list"),
        Route("POST", "/history/clear", "clear console events", "history_clear"),
        Route("POST", "/intake/flow", "record a flow and turbidity reading", "intake_flow"),
        Route("GET", "/intake/flow", "read the persisted flow reading", "intake_flow_get"),
        Route("POST", "/coag/dose", "update persisted flow and dose coagulant", "coag_dose"),
        Route("POST", "/coag/turbidity", "dose coagulant from mixed turbidity", "coag_turbidity"),
        Route("GET", "/coag/ratio", "read the current coagulant ratio", "coag_ratio"),
        Route("POST", "/flow/replace", "replace flow meter calibration", "flow_replace"),
        Route("POST", "/chlor/target", "update residual demand target", "chlor_target"),
        Route("POST", "/chlor/dose", "apply a chlorine dose", "chlor_dose"),
        Route("POST", "/clearwell/level", "adjust clear well level", "clearwell_level"),
        Route("POST", "/filter/add", "add a filter bed", "filter_add"),
        Route("POST", "/filter/close", "close a filter bed", "filter_close"),
        Route("POST", "/filter/open", "open a filter bed", "filter_open"),
        Route("POST", "/filter/renumber", "renumber a filter bed zone", "filter_renumber"),
        Route("POST", "/filter/load", "set a filter bed load", "filter_load"),
        Route("POST", "/filter/reset", "reset all filter bed closed states", "filter_reset"),
        Route("POST", "/filter/remove", "remove a filter bed", "filter_remove"),
        Route("POST", "/filter/zone", "read a filter bed zone", "filter_zone"),
        Route("POST", "/backwash/start", "start a backwash sequence", "backwash_start"),
        Route("GET", "/backwash/order", "compute filter duty rotation order", "backwash_order"),
        Route("POST", "/backwash/select", "select a bed by zone", "backwash_select"),
        Route("POST", "/backwash/enqueue", "enqueue a backwash command", "backwash_enqueue"),
        Route("POST", "/backwash/recover", "recover and discard stale commands", "backwash_recover"),
        Route("POST", "/backwash/replay", "replay queued backwash commands", "backwash_replay"),
        Route("POST", "/quota/add", "accumulate chlorine consumption", "quota_add"),
        Route("GET", "/quota", "read the chlorine accumulator", "quota_get"),
        Route("POST", "/quota/check", "check chemical quota usage", "quota_check"),
        Route("GET", "/audit", "list chemical dosing audit entries", "audit_list"),
        Route("GET", "/audit/summary", "audit entry count by kind", "audit_summary"),
        Route("POST", "/audit/filter", "filter audit entries by kind", "audit_filter"),
        Route("POST", "/cycle", "run a full treatment control cycle", "cycle"),
        Route("POST", "/simulate", "run deterministic simulation ticks", "simulate"),
        Route("GET", "/ph", "current ph reading and stable band", "ph_state"),
        Route("POST", "/ph/read", "record a ph reading", "ph_read"),
        Route("GET", "/ph/verdict", "ph stabilisation verdict", "ph_verdict"),
        Route("GET", "/schedule", "backwash schedule for the filter bank", "schedule_state"),
        Route("POST", "/schedule/threshold", "set the backwash load threshold", "schedule_threshold"),
        Route("POST", "/schedule/plan", "plan backwash order by bed load", "schedule_plan"),
        Route("GET", "/schedule/board", "capacity, window and rush aware schedule board", "board_state"),
        Route("POST", "/schedule/board/build", "rebuild the board from load, windows and crew", "board_build"),
        Route("POST", "/schedule/board/rush", "expedite a bed that has not started", "board_rush"),
        Route("POST", "/schedule/board/start", "start a scheduled wash idempotently", "board_start"),
        Route("POST", "/schedule/board/finish", "finish a running wash", "board_finish"),
        Route("POST", "/schedule/board/reconcile", "check ledger against actual drains", "board_reconcile"),
        Route("POST", "/schedule/board/capacity", "set available crews per slot", "board_capacity"),
        Route("POST", "/schedule/board/policy", "fix rush mode and overlap tie break", "board_policy"),
        Route("POST", "/schedule/window", "set a bed wash time window", "schedule_window"),
        Route("GET", "/intake/trend", "rolling intake flow statistics", "intake_trend"),
        Route("POST", "/intake/trend/window", "set the intake trend window", "intake_trend_window"),
        Route("POST", "/intake/trend/reset", "clear the intake flow trend", "intake_trend_reset"),
        Route("GET", "/audit/totals", "summed dose per chemical", "audit_totals"),
        Route("GET", "/inventory", "chemical inventory balance and lots", "inventory_state"),
        Route("POST", "/inventory/receive", "receive a chemical lot", "inventory_receive"),
        Route("POST", "/inventory/consume", "consume chemical stock oldest lot first", "inventory_consume"),
        Route("POST", "/inventory/reorder-level", "set the inventory reorder level", "inventory_reorder_level"),
        Route("GET", "/metrics", "console request counters", "metrics"),
        Route("POST", "/metrics/reset", "reset console request counters", "metrics_reset"),
        Route("GET", "/export/audit", "dosing audit trail as CSV", "export_audit"),
        Route("GET", "/export/telemetry", "telemetry counters as CSV", "export_telemetry"),
    ]


def register_routes(router: Router, handlers: ModuleType) -> None:
    """Bind every catalogued route to its handler function."""

    for route in route_table():
        router.add(route.method, route.path, getattr(handlers, route.handler))

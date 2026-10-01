"""Persisted intake flow and turbidity readings."""

from __future__ import annotations

from waterplant.store.epoch import load_float, save_float
from waterplant.store.store import Store

from .report import FlowState
from .sensor import Sensor

FLOW_KEY = "intake:flow"
TURBIDITY_KEY = "intake:turbidity"


class FlowRepository:
    """Stores the latest flow reading and the turbidity that came with it."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def persist_flow(self, value: float) -> None:
        """Write the flow reading before anything downstream consumes it."""

        save_float(self._store, FLOW_KEY, value)

    def load_flow(self) -> tuple[float, bool]:
        return load_float(self._store, FLOW_KEY)

    def record(self, sensor: Sensor) -> None:
        """Persist a full sensor reading atomically enough for the console."""

        self.persist_flow(sensor.flow)
        save_float(self._store, TURBIDITY_KEY, sensor.turbidity)

    def turbidity(self) -> float:
        value, _ = load_float(self._store, TURBIDITY_KEY)
        return value

    def state(self) -> FlowState:
        flow, present = self.load_flow()
        return FlowState(flow=flow, turbidity=self.turbidity(), present=present)

    def describe(self) -> str:
        state = self.state()
        return (
            f"intake flow={state.flow:.4f} turbidity={state.turbidity:.4f} "
            f"present={state.present}"
        )

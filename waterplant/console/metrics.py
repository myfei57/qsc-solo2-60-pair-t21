"""Request counters gathered by the console."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class MetricsSnapshot:
    """Counters accumulated since the last reset."""

    requests: int
    errors: int
    by_status: dict[int, int]
    by_route: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "requests": self.requests,
            "errors": self.errors,
            "by_status": {str(status): count for status, count in self.by_status.items()},
            "by_route": self.by_route,
        }


class Metrics:
    """Thread safe request counters for the running console."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requests = 0
        self._errors = 0
        self._by_status: dict[int, int] = {}
        self._by_route: dict[str, int] = {}

    def record(self, method: str, path: str, status: int) -> None:
        with self._lock:
            self._requests += 1
            self._by_status[status] = self._by_status.get(status, 0) + 1
            route = f"{method} {path}"
            self._by_route[route] = self._by_route.get(route, 0) + 1
            if status >= 400:
                self._errors += 1

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return MetricsSnapshot(
                requests=self._requests,
                errors=self._errors,
                by_status=dict(self._by_status),
                by_route=dict(self._by_route),
            )

    def reset(self) -> None:
        with self._lock:
            self._requests = 0
            self._errors = 0
            self._by_status = {}
            self._by_route = {}

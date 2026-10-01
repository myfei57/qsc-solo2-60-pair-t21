"""Audit projections."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditState:
    """Every audit entry plus the total count."""

    entries: list[dict[str, str]]
    count: int

    def as_dict(self) -> dict[str, object]:
        return {"entries": self.entries, "count": self.count}

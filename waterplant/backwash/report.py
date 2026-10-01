"""Backwash projections."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackwashState:
    """Queued commands and the beds that currently have an open drain."""

    commands: list[str]
    drains: list[str]

    def as_dict(self) -> dict[str, object]:
        return {"commands": self.commands, "drains": self.drains}

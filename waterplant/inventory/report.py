"""Inventory projections and validation rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InventoryState:
    """On hand balance plus the chemicals that need reordering."""

    reorder_level: float
    balance: float
    needs_reorder: list[str]
    lots: list[dict[str, object]]

    def as_dict(self) -> dict[str, object]:
        return {
            "reorder_level": self.reorder_level,
            "balance": self.balance,
            "needs_reorder": self.needs_reorder,
            "lots": self.lots,
        }


def validate_quantity(value: float, field: str = "amount") -> None:
    """Reject quantities outside the supported range."""

    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    if value > 1_000_000:
        raise ValueError(f"{field} exceeds the supported range")

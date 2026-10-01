"""Chemical inventory ledger.

Stock arrives in lots and is drawn down first in, first out so a partial
consumption always leaves the older lot in front. Every lot remembers how
much was received and how much has since been consumed, which keeps the
reorder calculation independent of the dosing audit trail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from waterplant.store.epoch import load_float, save_float
from waterplant.store.store import Store

from .report import InventoryState

LOTS_KEY = "inventory:lots"
REORDER_KEY = "inventory:reorder-level"
DEFAULT_REORDER_LEVEL = 25.0
EPSILON = 1e-9


@dataclass
class Lot:
    """One received batch of a chemical."""

    chemical: str
    lot_id: str
    received: float
    consumed: float = 0.0

    def remaining(self) -> float:
        return self.received - self.consumed

    def as_dict(self) -> dict[str, object]:
        return {
            "chemical": self.chemical,
            "lot_id": self.lot_id,
            "received": self.received,
            "consumed": self.consumed,
            "remaining": self.remaining(),
        }


class Inventory:
    """Receives and draws down chemical stock."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def _load(self) -> list[Lot]:
        raw, present = self._store.get(LOTS_KEY)
        if not present:
            return []
        try:
            parsed = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(parsed, list):
            return []
        lots: list[Lot] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            lots.append(
                Lot(
                    chemical=str(item.get("chemical", "")),
                    lot_id=str(item.get("lot_id", "")),
                    received=float(item.get("received", 0.0)),
                    consumed=float(item.get("consumed", 0.0)),
                )
            )
        return lots

    def _save(self, lots: list[Lot]) -> None:
        self._store.put(LOTS_KEY, json.dumps([lot.as_dict() for lot in lots], ensure_ascii=False))

    def receive(self, chemical: str, lot_id: str, amount: float) -> Lot:
        """Book a new lot into stock."""

        lots = self._load()
        lot = Lot(chemical=chemical, lot_id=lot_id, received=amount)
        lots.append(lot)
        self._save(lots)
        return lot

    def consume(self, chemical: str, amount: float) -> float:
        """Draw ``amount`` of a chemical down, oldest lot first."""

        lots = self._load()
        outstanding = amount
        for lot in lots:
            if lot.chemical != chemical:
                continue
            available = lot.remaining()
            if available <= EPSILON:
                continue
            taken = available if available < outstanding else outstanding
            lot.consumed += taken
            outstanding -= taken
            if outstanding <= EPSILON:
                break
        if outstanding > EPSILON:
            raise ValueError(f"insufficient {chemical} inventory")
        self._save(lots)
        return amount

    def lots(self, chemical: str | None = None) -> list[Lot]:
        lots = self._load()
        if chemical is None:
            return lots
        return [lot for lot in lots if lot.chemical == chemical]

    def chemicals(self) -> list[str]:
        names: list[str] = []
        for lot in self._load():
            if lot.chemical not in names:
                names.append(lot.chemical)
        return names

    def balance(self, chemical: str | None = None) -> float:
        return sum(lot.remaining() for lot in self.lots(chemical))

    def reorder_level(self) -> float:
        value, present = load_float(self._store, REORDER_KEY)
        return value if present else DEFAULT_REORDER_LEVEL

    def set_reorder_level(self, value: float) -> float:
        save_float(self._store, REORDER_KEY, value)
        return value

    def needs_reorder(self) -> list[str]:
        return [name for name in self.chemicals() if self.balance(name) < self.reorder_level()]

    def state(self) -> InventoryState:
        return InventoryState(
            reorder_level=self.reorder_level(),
            balance=self.balance(),
            needs_reorder=self.needs_reorder(),
            lots=[lot.as_dict() for lot in self.lots()],
        )

    def describe(self) -> str:
        state = self.state()
        return (
            f"inventory balance={state.balance:.4f} reorder={state.reorder_level:.4f} "
            f"alerts={len(state.needs_reorder)}"
        )

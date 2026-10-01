"""Numeric helpers layered on top of the string key/value store."""

from __future__ import annotations

from .store import Store


def load_float(store: Store, key: str) -> tuple[float, bool]:
    """Read a float value, reporting whether it was present and parseable."""

    raw, present = store.get(key)
    if not present:
        return 0.0, False
    try:
        return float(raw), True
    except ValueError:
        return 0.0, False


def save_float(store: Store, key: str, value: float) -> None:
    """Persist a float with a stable four decimal text form."""

    store.put(key, f"{value:.4f}")


def advance_epoch(store: Store, key: str, amount: float, cap: float) -> float:
    """Add ``amount`` to a counter and wrap it when it reaches ``cap``."""

    current, _ = load_float(store, key)
    next_value = current + amount
    if cap > 0 and next_value >= cap:
        next_value -= cap
    save_float(store, key, next_value)
    return next_value

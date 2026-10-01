"""Append only string lists stored inside the key/value document."""

from __future__ import annotations

import json

from .store import Store


def append_command(store: Store, list_key: str, value: str) -> None:
    """Append one item to the list stored under ``list_key``."""

    items = load_commands(store, list_key)
    items.append(value)
    store.put(list_key, json.dumps(items, ensure_ascii=False))


def load_commands(store: Store, list_key: str) -> list[str]:
    """Return the stored list, or an empty list when it is absent or corrupt."""

    raw, present = store.get(list_key)
    if not present:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    return [str(item) for item in items]


def clear_commands(store: Store, list_key: str) -> None:
    """Drop the whole list stored under ``list_key``."""

    store.delete(list_key)

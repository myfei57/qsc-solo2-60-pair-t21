"""Console event log endpoints."""

from __future__ import annotations

from waterplant.store import Store, append_event, clear_commands, event_count, has_key, list_events

from .http import Request, RequestError, Response, json_response

EVENT_KEY = "console:events"


def append(store: Store, request: Request) -> Response:
    kind = request.str_field("kind")
    if not kind:
        raise RequestError(400, "kind is required")
    append_event(store, EVENT_KEY, kind, request.str_field("value"))
    return json_response({"appended": True})


def listing(store: Store) -> Response:
    events = list_events(store, EVENT_KEY)
    return json_response(
        {
            "events": [event.as_dict() for event in events],
            "count": event_count(store, EVENT_KEY),
            "has_events": has_key(store, EVENT_KEY),
        }
    )


def clear(store: Store) -> Response:
    clear_commands(store, EVENT_KEY)
    return json_response({"cleared": True})

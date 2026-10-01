"""Build identity of the control console."""

from __future__ import annotations

from waterplant import __version__

BUILD_NAME = "waterplant-control"


def version_string() -> str:
    return f"{BUILD_NAME} {__version__}"


def version_payload() -> dict[str, str]:
    return {"name": BUILD_NAME, "version": __version__}

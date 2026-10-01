"""Host information exposed by the console."""

from __future__ import annotations

import os
import platform
import socket


def system_payload() -> dict[str, object]:
    """Describe the host the service is running on."""

    return {
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "processors": os.cpu_count() or 0,
    }

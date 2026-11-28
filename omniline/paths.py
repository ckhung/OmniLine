"""OmniLine user-data path conventions."""

from __future__ import annotations

import os
from pathlib import Path

HERMES_HOME_ENV = "HERMES_HOME"


def user_home() -> Path:
    """Return OmniLine's logical home, preferring ``HERMES_HOME``."""
    configured = os.environ.get(HERMES_HOME_ENV)
    return Path(configured).expanduser() if configured else Path.home()


def expand_user_path(value: str | Path) -> Path:
    """Expand ``~`` against OmniLine's logical home directory."""
    path = str(value)
    if path == "~":
        return user_home()
    if path.startswith("~/"):
        return user_home() / path[2:]
    return Path(path).expanduser()

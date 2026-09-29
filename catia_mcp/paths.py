"""Where the server keeps its own state (logs, lessons, lock file, popup log).

Nothing is ever written inside the installed package. Resolution order:
1. ``$CATIA_MCP_HOME``
2. ``%APPDATA%\\catia-mcp`` (Windows)
3. ``~/.catia-mcp``
"""

from __future__ import annotations

import os
from pathlib import Path


def home(create: bool = True) -> Path:
    env = os.environ.get("CATIA_MCP_HOME")
    if env:
        base = Path(env)
    elif os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / "catia-mcp"
    else:
        base = Path.home() / ".catia-mcp"
    if create:
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return base


def log_file() -> Path:
    return home() / "catia_mcp.log"


def lock_file() -> Path:
    return home() / "catia.lock"


def popup_log() -> Path:
    return home() / "catia_popups.log"


def designation_cache_file() -> Path:
    return home() / "designation_cache.jsonl"


def env_flag(name: str, default: bool) -> bool:
    """Boolean environment switch: 1/true/yes/on and 0/false/no/off."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

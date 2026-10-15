"""Runtime paths for travel-mode configuration and state."""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    return (Path(root).expanduser() if root else Path.home() / ".config") / "travel-mode"


def state_dir() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    return (Path(root).expanduser() if root else Path.home() / ".local" / "state") / "travel-mode"


def config_path() -> Path:
    return Path(os.environ.get("TRAVEL_MODE_CONFIG", config_dir() / "config.toml")).expanduser()


def state_path() -> Path:
    return Path(os.environ.get("TRAVEL_MODE_STATE", state_dir() / "state.json")).expanduser()


def legacy_conf_path() -> Path:
    return Path.home() / ".travel-mode.conf"


def legacy_state_path() -> Path:
    return Path.home() / ".travel-mode.state"


CONFIG_PATH = config_path()
STATE_PATH = state_path()
LEGACY_CONF_PATH = legacy_conf_path()
LEGACY_STATE_PATH = legacy_state_path()


__all__ = [
    "CONFIG_PATH",
    "LEGACY_CONF_PATH",
    "LEGACY_STATE_PATH",
    "STATE_PATH",
    "config_dir",
    "config_path",
    "legacy_conf_path",
    "legacy_state_path",
    "state_dir",
    "state_path",
]

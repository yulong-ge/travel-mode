"""Owned LaunchAgent configuration and bounded, local event receipts."""

from __future__ import annotations

import json
import math
import os
import plistlib
import sysconfig
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from xml.parsers.expat import ExpatError

from .i18n import t
from .paths import config_path, state_path
from .state import StateError
from .system.shell import run_rc

LABEL = "io.github.yulong-ge.travel-mode.events"
NOTIFICATION = "com.apple.system.powersources.source"
PLIST_KEYS = {
    "Label",
    "ProgramArguments",
    "EnvironmentVariables",
    "LaunchEvents",
    "ThrottleInterval",
    "ProcessType",
}
_preview_paths = ContextVar("automation_preview_paths", default=None)


class AutomationError(RuntimeError):
    pass


def agent_path():
    if _preview_paths.get() is not None:
        return _preview_paths.get()[0]
    return Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


@contextmanager
def preview_scope():
    """Preserve review targets while the generic dry-run lifecycle isolates HOME."""
    token = _preview_paths.set((agent_path(), config_path(), state_path()))
    try:
        yield
    finally:
        _preview_paths.reset(token)


def log_path():
    return state_path().parent / "automation.json"


def executable():
    path = Path(sysconfig.get_path("scripts")) / "travel-mode"
    if not path.is_file() or not os.access(path, os.X_OK):
        raise AutomationError(t("automation.executable_missing", path=path))
    return str(path.resolve())


def agent_plist(program=None):
    program = program or executable()
    if not Path(program).is_absolute():
        raise AutomationError(t("automation.absolute_program"))
    paths = _preview_paths.get()
    config, state = paths[1:] if paths else (config_path(), state_path())
    return {
        "Label": LABEL,
        "ProgramArguments": [program, "event", "power-changed", "--launch-event"],
        "EnvironmentVariables": {
            "TRAVEL_MODE_CONFIG": str(config.absolute()),
            "TRAVEL_MODE_STATE": str(state.absolute()),
        },
        "LaunchEvents": {"com.apple.notifyd.matching": {"power-changed": {"Notification": NOTIFICATION}}},
        "ThrottleInterval": 30,
        "ProcessType": "Background",
    }


def read_agent():
    path = agent_path()
    if not path.exists():
        return None
    try:
        data = plistlib.loads(path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError) as exc:
        raise AutomationError(f"{path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) != PLIST_KEYS:
        raise AutomationError(t("automation.foreign_plist", path=path))
    args = data.get("ProgramArguments")
    if (
        not isinstance(args, list)
        or len(args) != 4
        or not isinstance(args[0], str)
        or not Path(args[0]).is_absolute()
    ):
        raise AutomationError(t("automation.foreign_plist", path=path))
    environment = data.get("EnvironmentVariables")
    if (
        not isinstance(environment, dict)
        or set(environment) != {"TRAVEL_MODE_CONFIG", "TRAVEL_MODE_STATE"}
        or any(not isinstance(value, str) or not Path(value).is_absolute() for value in environment.values())
    ):
        raise AutomationError(t("automation.foreign_plist", path=path))
    expected = agent_plist(args[0])
    expected["EnvironmentVariables"] = environment
    if data != expected:
        raise AutomationError(t("automation.foreign_plist", path=path))
    return data


def atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_log():
    path = log_path()
    if not path.exists():
        return {"version": 1, "entries": []}
    try:
        data = json.loads(path.read_text())
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "entries"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["entries"], list)
        ):
            raise ValueError("invalid event log")
        for row in data["entries"]:
            if (
                not isinstance(row, dict)
                or set(row) != {"at", "source", "result", "details"}
                or type(row["at"]) not in (int, float)
                or not math.isfinite(row["at"])
                or row["at"] < 0
                or row["source"] not in (None, "battery", "ac", "ups")
                or row["result"] not in ("handled", "ignored", "failed")
                or not isinstance(row["details"], list)
                or any(not isinstance(s, str) for s in row["details"])
            ):
                raise ValueError("invalid event receipt")
        return data
    except (OSError, ValueError) as exc:
        raise StateError(f"{path}: {exc}") from exc


def append_log(row, retain):
    data = read_log()
    data["entries"] = [*data["entries"], row][-retain:]
    try:
        atomic_write(log_path(), (json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n").encode())
    except (OSError, ValueError) as exc:
        raise StateError(f"{log_path()}: {exc}") from exc


def service_status():
    output, rc = run_rc(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], timeout=5)
    if rc in (3, 113):
        return False, False
    if rc:
        raise AutomationError(t("json.command_failed", command="launchctl print", rc=rc, detail=output))
    import re

    state = re.search(r"^\s*state = (.+)$", output, re.M)
    if state is None:
        raise AutomationError(t("automation.native_failed", detail="launchctl print: missing state"))
    return True, state[1].strip() == "running"


def status():
    plist = read_agent()
    loaded, running = service_status()
    entries = read_log()["entries"]
    return {
        "version": 1,
        "installed": plist is not None,
        "loaded": loaded,
        "running": running,
        "path": str(agent_path()),
        "plist": plist,
        "last_event": entries[-1] if entries else None,
    }

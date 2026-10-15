"""Snapshot state persistence with legacy compatibility."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .constants import LIST_KEYS, STR_KEYS
from .i18n import t
from .paths import state_path

STATE_PATH = state_path()
_INITIAL_STATE_PATH = STATE_PATH
STATE_VERSION = 3
_STATE_PATH_OVERRIDE: Path | None = None
STATE_LOCK = threading.RLock()


def state_snapshot(state: dict) -> dict:
    with STATE_LOCK:
        return deepcopy(state)


class StateError(ValueError):
    """状态快照无法读取或写入。"""


def _path(path=None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    if _STATE_PATH_OVERRIDE is not None:
        return _STATE_PATH_OVERRIDE
    from .system.shell import dry_run_state_dir, is_dry_run

    if is_dry_run():
        return dry_run_state_dir() / "state.json"
    if STATE_PATH != _INITIAL_STATE_PATH:
        return Path(STATE_PATH)
    return state_path()


def set_state_path_override(path: str | Path | None) -> None:
    global _STATE_PATH_OVERRIDE
    _STATE_PATH_OVERRIDE = Path(path).expanduser() if path is not None else None


def parse_legacy_state(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        match = re.match(r'(\w+)="(.*)"', line.strip())
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2)
        if key in (*STR_KEYS, "saved_at"):
            result[key] = value
        elif key in LIST_KEYS:
            result[key] = [item for item in value.split("|") if item]
    return result


def load_state(path=None):
    target = _path(path)
    if not target.exists():
        return None
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StateError(f"{target}: read: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as json_error:
        raw = parse_legacy_state(text)
        if text.strip() and not raw:
            raise StateError(t("state.state_file_is_corrupt_inspect_or", v0=target)) from json_error
    if not isinstance(raw, dict):
        raise StateError(t("state.state_file_is_not_a_json", v0=target))
    version = raw.get("version", 1)
    if not isinstance(version, int):
        raise StateError(t("state.state_file_version_must_be_an", v0=target))
    if version > STATE_VERSION:
        raise StateError(
            t("state.state_file_uses_unknown_newer_version", v0=target, v1=version, v2=STATE_VERSION)
        )
    result = {key: [] for key in LIST_KEYS}
    for key in LIST_KEYS:
        value = raw.get(key, [])
        if isinstance(value, str):
            value = [item for item in value.split("|") if item]
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise StateError(t("state.state_file_must_be_a_list", v0=target, v1=key))
        result[key] = list(value)
    for key in (*STR_KEYS, "saved_at", "profile", "scenario"):
        value = raw.get(key, "")
        if not isinstance(value, str):
            raise StateError(t("safety.expected_string", path=target, field=key, kind=type(value).__name__))
        if key != "scenario" or value:
            result[key] = value
    if result.get("scenario", "") not in ("", "work", "away"):
        raise StateError(f"{target}: scenario: expected work or away")
    origins = raw.get("power_origin", {})
    if not isinstance(origins, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in origins.items()
    ):
        raise StateError(t("safety.expected_mapping", path=target, field="power_origin"))
    result["power_origin"] = origins
    receipts = raw.get("frozen_pids", [])
    if not isinstance(receipts, list) or any(
        not isinstance(item, dict)
        or set(item) != {"pid", "started", "command", "path"}
        or type(item["pid"]) is not int
        or item["pid"] <= 0
        or any(not isinstance(item[key], str) or not item[key] for key in ("started", "command", "path"))
        for item in receipts
    ):
        raise StateError(t("safety.invalid_receipt", path=target, field="frozen_pids"))
    if "frozen_pids" in raw:
        result["frozen_pids"] = receipts
    if "legacy_frozen_apps" in raw:
        legacy = raw["legacy_frozen_apps"]
        if not isinstance(legacy, list) or any(not isinstance(item, str) or not item for item in legacy):
            raise StateError(t("safety.expected_bundle_paths", path=target, field="legacy_frozen_apps"))
        result["legacy_frozen_apps"] = legacy
    pending = raw.get("pending_commands", [])
    if not isinstance(pending, list) or any(
        not isinstance(cmd, list) or not cmd or any(not isinstance(part, str) for part in cmd)
        for cmd in pending
    ):
        raise StateError(t("safety.invalid_receipt", path=target, field="pending_commands"))
    result["pending_commands"] = pending
    if "measurement" in raw:
        from .measurement import validate_comparison

        try:
            validate_comparison(raw["measurement"])
        except ValueError as exc:
            raise StateError(f"{target}: measurement: {exc}") from exc
        result["measurement"] = raw["measurement"]
    result["version"] = version
    return result


def save_state(state: dict, path=None):
    target = _path(path)
    with STATE_LOCK:
        for key in LIST_KEYS:
            values = state.setdefault(key, [])
            seen = set()
            state[key] = [item for item in values if not (item in seen or seen.add(item))]
        if not state.get("anchored_at"):
            state["anchored_at"] = state.get("saved_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state["version"] = STATE_VERSION
        payload = json.dumps(state, ensure_ascii=False, indent=1) + "\n"
    tmp = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=target.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            tmp = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)  # noqa: PTH105 - explicitly use atomic os.replace on a unique sibling
    except OSError as exc:
        raise StateError(t("state.writing_state_file_failed", v0=target, v1=exc)) from exc
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def new_state() -> dict:
    result = {key: [] for key in LIST_KEYS}
    result.update({key: "" for key in STR_KEYS})
    result.update({"saved_at": "", "profile": "", "version": STATE_VERSION})
    result.update({"power_origin": {}, "frozen_pids": []})
    return result


def snapshot_origin():
    previous = load_state()
    if previous:
        return previous, True
    return new_state(), False


def profile_string(profs, profile):
    from .constants import POWER_KEY_ORDER

    parts = []
    for key in (*POWER_KEY_ORDER, "powermode"):
        value = profs.get(profile, {}).get(key)
        if value is not None:
            parts.extend((key, value))
    return " ".join(parts)


def anchor_age_hours(state):
    for key in ("anchored_at", "saved_at"):
        try:
            timestamp = datetime.strptime(state[key], "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - timestamp).total_seconds() / 3600
        except (KeyError, ValueError):
            continue
    return None


__all__ = [
    "STATE_PATH",
    "STATE_VERSION",
    "StateError",
    "anchor_age_hours",
    "load_state",
    "new_state",
    "parse_legacy_state",
    "profile_string",
    "save_state",
    "set_state_path_override",
    "snapshot_origin",
]

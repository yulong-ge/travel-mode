"""One-shot observations of recorded pauses; remediation uses existing Actions."""

from __future__ import annotations

import plistlib
from pathlib import Path
from xml.parsers.expat import ExpatError

from .actions.apps import FreezeApp, QuitApp, QuitDaemonApp, UnloadAgent, configured_daemon
from .i18n import t
from .state import StateError
from .system import launchd, process_control


def was_paused(state, row):
    return any(
        receipt["kind"] == row["kind"]
        and receipt["path"] == row["path"]
        and (row["kind"] != "pid" or receipt["pid"] == row["pid"])
        for receipt in state.get("confirmed_pauses", [])
    )


def detect(state):
    if not state or not state.get("confirmed_pauses"):
        return []
    result = []
    if state.get("unloaded_svcs"):
        loaded = launchd._loaded(launchd.launchctl_names())
        for path in dict.fromkeys(state["unloaded_svcs"]):
            try:
                metadata = plistlib.loads(Path(path).read_bytes())
                label = metadata["Label"]
                if not isinstance(label, str) or not label:
                    raise ValueError(t("resurrection.invalid_label"))
            except (OSError, ValueError, KeyError, TypeError, ExpatError) as exc:
                raise StateError(f"{path}: unloaded_svcs.Label: {exc}") from exc
            candidate = {"kind": "agent", "path": path, "name": label}
            if label in loaded and was_paused(state, candidate):
                result.append(candidate)
    if not state.get("quit_apps") and not state.get("frozen_pids"):
        return result
    rows = process_control.process_table()
    for path in dict.fromkeys(state.get("quit_apps", [])):
        prefixes = process_control._prefixes(path)
        if was_paused(state, dict(kind="app", path=path)) and any(
            row.command.startswith(prefixes) for row in rows
        ):
            result.append({"kind": "app", "path": path, "name": Path(path).stem})
    for receipt in state.get("frozen_pids", []):
        row = next((row for row in rows if process_control._same_process(row, receipt)), None)
        if (
            row is not None
            and not row.stat.startswith("T")
            and dict(kind="pid", **receipt) in state["confirmed_pauses"]
        ):
            result.append(
                {"kind": "pid", "path": receipt["path"], "name": Path(receipt["path"]).stem, "pid": row.pid}
            )
    return result


def describe(row):
    return t(f"resurrection.{row['kind']}", name=row["name"], pid=row.get("pid", ""))


def reapply(state, execute, save):
    # Re-observe at admission, then existing Actions revalidate identities before mutation.
    seen = set()
    failures = 0
    for row in detect(state):
        key = row["kind"], row["path"]
        if key in seen:
            continue
        seen.add(key)
        if row["kind"] == "agent":
            _, rc = execute(UnloadAgent(row["path"]))
            ok = rc == 0
        elif row["kind"] == "pid":
            ok = execute(FreezeApp(row["path"]))
        else:
            spec = configured_daemon(row["name"])
            if spec is not None:
                _, ok, _ = execute(QuitDaemonApp(spec, state, save))
            else:
                ok = execute(QuitApp(row["path"], row["name"]))
        if not ok:
            failures += 1
        save(state)
    return failures

"""Finite read-only health observations shared by CLI, JSON and TUI.

No timers, background monitors or mutation commands live here. Each check has a
two-second deadline; shared reads are cached within a fifteen-second report.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from time import monotonic

from ..categories import identify
from ..config import load_config
from ..i18n import t
from . import pmset
from .shell import run_rc

PROCESS_COMMAND = ["ps", "-axo", "pid=,ppid=,uid=,stat=,%cpu=,lstart=,comm="]
BUDGET = 15.0
CHECK_TIMEOUT = 2.0
SYSTEM_ROOTS = ("/System/", "/usr/", "/bin/", "/sbin/", "/Library/Apple/")
SYSTEM_OWNERS = {"powerd", "WindowServer", "kernel_task", "loginwindow", "bluetoothd", "mds", "mds_stores"}
PROTECTED_APPS = {"Terminal", "iTerm", "iTerm2", "Warp", "Alacritty", "kitty"}


class ObservationError(RuntimeError):
    """A bounded observation could not produce a valid result."""


class CapabilityUnavailable(ObservationError):
    """A valid observation does not advertise this optional capability."""


def native_command(operation):
    return [sys.executable, "-m", "travel_mode.system.health_native", operation]


def parse_processes(output):
    if not output.strip():
        raise ObservationError("ps: empty process snapshot")
    rows = []
    for line in output.splitlines():
        fields = line.split(None, 10)
        try:
            if len(fields) != 11:
                raise ValueError("expected eleven fields")
            pid, ppid, uid = map(int, fields[:3])
            cpu = float(fields[4])
            # macOS ps uses signed UID values for accounts such as nobody (-2).
            if min(pid, ppid) < 0 or not -(2**31) <= uid < 2**32 or not math.isfinite(cpu) or cpu < 0:
                raise ValueError("invalid PID, UID or CPU")
        except ValueError as exc:
            raise ObservationError(f"ps row {line!r}: {exc}") from exc
        rows.append(
            dict(
                pid=pid,
                ppid=ppid,
                uid=uid,
                stat=fields[3],
                cpu=cpu,
                started=" ".join(fields[5:10]),
                executable=fields[10],
            )
        )
    return rows


def app_path(executable):
    from .app_bundle import bundle_for_executable

    if executable.startswith(SYSTEM_ROOTS) or not re.match(
        r".+?\.app/Contents/(?:MacOS|Frameworks|Helpers)/", executable, re.IGNORECASE
    ):
        return None
    return bundle_for_executable(executable)


class Session:
    def __init__(self, config, clock):
        self.config, self.clock = config, clock
        self.started = clock()
        self.deadline = self.started + BUDGET
        self.check_deadline = self.deadline
        self.cache = {}
        self.subjects = {}
        self.foreground_error = None
        self.foreground = None

    def read(self, command):
        remaining = min(self.deadline, self.check_deadline) - self.clock()
        if remaining <= 0:
            raise ObservationError(t("health.deadline"))
        key = tuple(command)
        if key in self.cache:
            value = self.cache[key]
            if isinstance(value, ObservationError):
                raise value
            return value
        output, rc = run_rc(command, timeout=remaining)
        if rc:
            error = ObservationError(f"{' '.join(command)}: rc={rc}: {output.strip()}")
            self.cache[key] = error
            raise error
        self.cache[key] = output
        return output

    def native(self, operation):
        output = self.read(native_command(operation))
        try:
            return json.loads(output)
        except ValueError as exc:
            raise ObservationError(f"{operation}: invalid JSON: {output!r}") from exc

    def processes(self):
        return parse_processes(self.read(PROCESS_COMMAND))

    def battery(self):
        output = self.read(["pmset", "-g", "ps"])
        if "Battery Power" in output:
            return True
        if "AC Power" in output or "UPS Power" in output:
            return False
        raise ObservationError(f"pmset -g ps: unknown power source: {output!r}")

    def assertions(self):
        output = self.read(["pmset", "-g", "assertions"])
        if "Assertion status system-wide:" not in output:
            raise ObservationError(f"pmset assertions: missing status header: {output!r}")
        for line in output.splitlines():
            if (
                line.lstrip().startswith("pid ")
                and re.search(r"Prevent(?:UserIdle)?SystemSleep", line)
                and not re.search(r"\bpid\s+\d+\s*\([^)]+\)", line)
            ):
                raise ObservationError(f"pmset assertions: malformed holder: {line!r}")
        return [r for r in pmset.sleep_blocking_assertions(output.splitlines()) if r["pid"]]

    def allowed_app(self, row):
        path = app_path(row["executable"])
        if not path or row["uid"] != os.getuid() or row["executable"].startswith(SYSTEM_ROOTS):
            return None
        name = Path(path).stem
        config = self.config
        rule = identify(name=name, executable=row["executable"])
        from ..categories import ignored, protected

        if name in PROTECTED_APPS or name in (*config.apps.skip, *config.apps.ignore):
            return None
        if rule and (rule["action"] == "notice" or ignored(rule, config) or protected(rule, config)):
            return None
        return path

    def app_action(self, row, kind="quit"):
        path = self.allowed_app(row)
        return dict(kind=kind, path=path, name=Path(path).stem) if path else None


def sleep_disabled(s):
    output = s.read(["pmset", "-g"])
    from .shell import ReadError

    try:
        value = pmset.parse_sleep_disabled(output)
    except ReadError as exc:
        raise ObservationError(str(exc)) from exc
    if value is None:
        raise CapabilityUnavailable(t("power.sleep_switch_reason"))
    hit = value == "1"
    return hit, [f"SleepDisabled={value}"], [dict(kind="sleep")] if hit else []


def keep_awake(s):
    rows = s.processes()
    by_pid = {r["pid"]: r for r in rows}
    evidence, actions = [], []
    holders = s.assertions()  # Validate before admitting any PID action.
    subjects = s.subjects.setdefault("keep_awake", [])
    owners = {
        int(match[1])
        for line in s.read(["pmset", "-g", "assertions"]).splitlines()
        if re.search(r"\bPrevent(?:UserIdle)?(?:System|Display)Sleep\b", line)
        and (match := re.search(r"\bpid\s+(\d+)\s*\([^)]+\)", line))
    }
    seen = set()
    for row in rows:
        path = app_path(row["executable"])
        name = Path(path).stem if path else ""
        rule = identify(name=name, executable=row["executable"])
        if Path(row["executable"]).name == "caffeinate":
            command = s.read(["ps", "-ww", "-p", str(row["pid"]), "-o", "command="]).strip()
            if not command:
                raise ObservationError(f"caffeinate PID {row['pid']}: command line disappeared")
            parent = by_pid.get(row["ppid"])
            parent_executable = parent["executable"] if parent else t("caffeinate.parent_exited")
            evidence.append(f"PID {row['pid']}: {command}; PPID {row['ppid']}: {parent_executable}")
            from ..caffeinate_view import provenance

            subjects.append(provenance(row, by_pid, command, holders))
            if row["pid"] in owners and row["uid"] == os.getuid():
                actions.append(
                    dict(
                        kind="caffeinate",
                        process={key: row[key] for key in ("pid", "ppid", "stat", "started", "executable")},
                        command_line=command,
                        parent=parent_executable,
                    )
                )
        elif rule and rule["category"] == "keep-awake" and path not in seen:
            seen.add(path)
            evidence.append(f"{name}: PID {row['pid']}, {path}")
            action = s.app_action(row)
            if action:
                actions.append(action)
    hit = bool(evidence)
    return hit, evidence or [t("health.process_count", count=len(rows))], actions


def assertions(s):
    rows = {r["pid"]: r for r in s.processes()}
    evidence, actions, seen = [], [], set()
    holders = s.assertions()
    for holder in holders:
        row = rows.get(int(holder["pid"]))
        if holder["process"] in SYSTEM_OWNERS:
            continue
        if (
            row
            and row["executable"].startswith(SYSTEM_ROOTS)
            and Path(row["executable"]).name != "caffeinate"
        ):
            # Preserve caffeinate assertions for readiness; its action is owned
            # by keep_awake and presentation deduplicates the shared PID.
            continue
        evidence.append(holder["line"])
        action = s.app_action(row) if row else None
        if action and action["path"] not in seen:
            actions.append(action)
            seen.add(action["path"])
    hit = bool(evidence)
    return hit, evidence or [t("health.assertion_count", count=len(holders))], actions


def displays(s):
    rows = s.native("displays")
    if not isinstance(rows, list) or any(
        not isinstance(r, dict) or type(r.get("id")) is not int or type(r.get("builtin")) is not bool
        for r in rows
    ):
        raise ObservationError(f"CoreGraphics displays: invalid data: {rows!r}")
    external = [r for r in rows if not r["builtin"]]
    evidence = [f"CoreGraphics display ID {r['id']}" for r in external]
    return bool(external), evidence or [t("health.display_count", count=len(rows))], []


def spotlight(s):
    output = s.read(["mdutil", "-s", "/"])
    if "Indexing enabled." in output:
        enabled = True
    elif "Indexing disabled." in output:
        enabled = False
    else:
        raise ObservationError(f"mdutil -s /: unknown indexing state: {output!r}")
    rows = [
        r
        for r in s.processes()
        if re.fullmatch(r"mds(?:_stores)?|mdworker(?:_shared)?", Path(r["executable"]).name)
    ]
    cpu = sum(r["cpu"] for r in rows)
    hit = enabled and cpu >= s.config.health.spotlight_cpu_threshold
    evidence = [output.strip(), f"ps %CPU sum={cpu:g}"]
    evidence += [f"PID {r['pid']}: {r['executable']}, %CPU={r['cpu']:g}" for r in rows]
    return hit, evidence, [dict(kind="spotlight")] if hit else []


def backup(s):
    output = s.read(["tmutil", "status"])
    match = re.search(r"\bRunning\s*=\s*([01])\s*;", output)
    if not match:
        raise ObservationError(f"tmutil status: missing or invalid Running: {output!r}")
    return match[1] == "1", [f"Time Machine Running={match[1]}"], []


def low_power(s):
    if not s.battery():
        return False, [t("health.on_ac")], []
    output = s.read(["pmset", "-g"])
    from .shell import ReadError

    try:
        for key in ("lowpowermode", "powermode"):
            value = pmset.parse_optional_setting(output, key, {"0", "1", "2"})
            if value is not None:
                break
        else:
            raise CapabilityUnavailable(t("power.low_power_unavailable"))
        if key not in pmset.parse_custom(s.read(["pmset", "-g", "custom"])).get("b", {}):
            raise CapabilityUnavailable(t("power.battery_unavailable"))
    except ReadError as exc:
        raise ObservationError(str(exc)) from exc
    hit = value != "1"
    return hit, [f"{key}={value}"], [dict(kind="low_power", key=key)] if hit else []


def brightness(s):
    if not s.battery():
        return False, [t("health.on_ac")], []
    value = s.native("brightness")
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ObservationError(f"DisplayServices brightness: invalid value {value!r}")
    hit = value > s.config.health.brightness_threshold
    from ..profiles import active_values

    target = active_values().get("brightness", s.config.health.brightness_target)
    target = float(target) if target is not None else s.config.health.brightness_target
    return (
        hit,
        [f"brightness={value:g}; threshold={s.config.health.brightness_threshold:g}"],
        [dict(kind="brightness", value=min(target, value))] if hit else [],
    )


def high_cpu(s):
    from .lsappinfo import ForegroundError, foreground

    rows = s.processes()

    def read(command):
        from .shell import ReadError

        try:
            return s.read(command)
        except ObservationError as exc:
            raise ReadError(str(exc)) from exc

    try:
        front = foreground(read)
        s.foreground = front.as_dict()
        if not any(r["pid"] == front.pid for r in rows):
            raise ObservationError(f"lsappinfo: foreground PID {front.pid} disappeared from ps snapshot")
    except (ForegroundError, ObservationError) as exc:
        s.foreground_error = str(exc)
        raise ObservationError(str(exc)) from exc
    foreground = front.pid
    front_paths = {app_path(r["executable"]) for r in rows if r["pid"] == foreground} - {None}
    own = {os.getpid()}
    by_pid = {r["pid"]: r for r in rows}
    cursor = os.getpid()
    while cursor in by_pid and by_pid[cursor]["ppid"] not in own:
        cursor = by_pid[cursor]["ppid"]
        own.add(cursor)
    evidence, actions, seen = [], [], set()
    subjects = s.subjects.setdefault("high_cpu", [])
    for row in sorted(rows, key=lambda r: r["cpu"], reverse=True):
        path = app_path(row["executable"])
        if (
            row["cpu"] < s.config.health.cpu_threshold
            or row["uid"] != os.getuid()
            or row["pid"] in own | {foreground}
            or row["stat"].startswith("T")
            or row["executable"].startswith(SYSTEM_ROOTS)
            or (path and Path(path).stem in PROTECTED_APPS)
        ):
            continue
        if path and path in front_paths:
            continue
        rule = identify(name=Path(path).stem if path else "", executable=row["executable"])
        category = rule["category"] if rule else t("health.uncategorized")
        evidence.append(f"PID {row['pid']}: {row['executable']}, %CPU={row['cpu']:g} ({category})")
        subjects.append(
            dict(
                name=Path(path).stem if path else Path(row["executable"]).name,
                pid=row["pid"],
                cpu=row["cpu"],
                executable=row["executable"],
                category=category,
            )
        )
        action = s.app_action(row, "quit" if rule and rule["action"] == "quit" else "freeze")
        if action and action["path"] not in seen:
            seen.add(action["path"])
            actions.append(action)
        if len(evidence) == 5:
            break
    hit = bool(evidence)
    return (
        hit,
        evidence or [t("health.cpu_clear", threshold=s.config.health.cpu_threshold, pid=foreground)],
        actions,
    )


CHECKS = [
    ("sleep_disabled", sleep_disabled),
    ("keep_awake", keep_awake),
    ("assertions", assertions),
    ("displays", displays),
    ("spotlight", spotlight),
    ("backup", backup),
    ("low_power", low_power),
    ("brightness", brightness),
    ("high_cpu", high_cpu),
]


def collect(config=None, *, clock=None):
    clock = clock or monotonic
    s = Session(config or load_config(), clock)
    checks = []
    for key, probe in CHECKS:
        started = clock()
        s.check_deadline = min(s.deadline, started + CHECK_TIMEOUT)
        row = dict(
            id=key,
            title=t(f"health.{key}.title"),
            impact=t(f"health.{key}.impact"),
            manual=t(f"health.{key}.manual"),
            evidence=[],
            actions=[],
            error=None,
        )
        try:
            if started >= s.deadline:
                raise ObservationError(t("health.deadline"))
            hit, row["evidence"], row["actions"] = probe(s)
            row["status"] = "hit" if hit else "clear"
        except CapabilityUnavailable as exc:
            row.update(status="unavailable", evidence=[str(exc)], actions=[])
        except ObservationError as exc:
            row.update(status="unknown", error=str(exc))
            from ..failures import record

            record(None, row["title"], str(exc), kind="observation", store=False)
        row["elapsed_seconds"] = round(clock() - started, 4)
        if key in s.subjects:
            row["subjects"] = s.subjects[key]
        checks.append(row)
    result = dict(
        checks=checks,
        elapsed_seconds=round(clock() - s.started, 4),
        budget_seconds=BUDGET,
        limitations=[t("health.omitted"), t("health.cpu_method")],
    )
    if s.foreground_error:
        message = t("setup.foreground_blocked", reason=s.foreground_error)
        result["app_safety"] = dict(status="blocked", error=s.foreground_error, message=message)
        for row in checks:
            row["actions"] = [a for a in row["actions"] if a["kind"] not in {"quit", "freeze"}]
            if row["id"] == "high_cpu":
                row["safety_reason"] = message
    if s.foreground:
        result["limitations"].append(t("health.foreground_source", source=s.foreground["source"]))
        if s.foreground["fallback_reason"]:
            result["limitations"].append(s.foreground["fallback_reason"])
    from ..health_view import assertion_context
    from ..state import load_state

    raw_source = s.cache.get(("pmset", "-g", "ps"))
    external = None
    if isinstance(raw_source, str):
        external = True if "AC Power" in raw_source else False if "Battery Power" in raw_source else None
    snapshot = load_state()
    context = assertion_context(snapshot, external=external)
    if context:
        context["scenario"] = snapshot.get("scenario") or None
        result["assertion_context"] = context
    return result

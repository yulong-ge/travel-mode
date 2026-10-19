"""Bounded local failure receipts, command observations and explicit batch results."""

from __future__ import annotations

import fcntl
import json
import os
import sys
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime

from .i18n import t
from .state import StateError, _path

MAX_LOG_BYTES = 1024 * 1024
MAX_FAILURES = 50
_commands = ContextVar("action_commands", default=None)
_batch = ContextVar("action_batch", default=None)
_reporter = ContextVar("action_failure_reporter", default=None)


class CommandError(RuntimeError):
    """Expected external command failure, distinct from a programming error."""

    def __init__(self, command, rc, detail):
        self.command, self.rc, self.detail = list(command), rc, detail
        super().__init__(t("json.command_failed", command=" ".join(command), rc=rc, detail=detail))


class BatchStopped(RuntimeError):
    """The interface already displayed/logged this error; stop this transaction."""


def log_path():
    return _path().parent / "travel-mode.log"


def short(value, limit=2000):
    return str(value)[:limit]


def observe(command, result):
    rows = _commands.get()
    if rows is not None:
        rows.append(dict(command=list(command), rc=result.rc, stdout=result.stdout, stderr=result.stderr))
        del rows[:-64]


@contextmanager
def capture():
    rows = []
    token = _commands.set(rows)
    try:
        yield rows
    finally:
        _commands.reset(token)


def append_log(row, error=None):
    """Serialize append/rotation across processes; never record env or stdin."""
    path = log_path()
    data = dict(row)
    if error is not None:
        data["traceback"] = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    payload = (json.dumps(data, ensure_ascii=False) + "\n").encode()
    if len(payload) > MAX_LOG_BYTES:
        raise StateError(f"{path}: {t('failure.log_too_large', limit=MAX_LOG_BYTES)}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        lock = os.open(path.with_suffix(".log.lock"), flags, 0o600)
        try:
            os.fchmod(lock, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise OSError("refusing a non-regular log file")
            if path.exists() and path.stat().st_size + len(payload) > MAX_LOG_BYTES:
                path.chmod(0o600)
                path.replace(path.with_suffix(".log.1"))
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "ab") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(lock)
    except OSError as exc:
        raise StateError(f"{path}: {t('failure.log_write', reason=exc)}") from exc


def record(
    state,
    action,
    reason,
    commands=(),
    *,
    error=None,
    kind="action",
    target="",
    save=None,
    store=True,
    quit_status=None,
):
    from .sessions import utc_now

    failed = next((r for r in reversed(commands) if r["rc"]), commands[-1] if commands else {})
    row = dict(
        at=utc_now(),
        scenario=(state or {}).get("scenario") or None,
        action=short(action, 200),
        target=short(target, 512),
        command=[short(p, 512) for p in failed.get("command", [])[:32]],
        rc=failed.get("rc"),
        reason=short(reason),
        stdout=short(failed.get("stdout", "")),
        stderr=short(failed.get("stderr", "")),
        kind=kind,
    )
    if quit_status is not None:
        row["quit_status"] = quit_status
    if state and "session" in state:
        row["session_id"] = state["session"]["id"]
        row["counts"] = dict(state["session"]["actions"])
    append_log(row, error)
    if state is not None and store:
        rows = state.setdefault("action_failures", [])
        rows.append(row)
        del rows[:-MAX_FAILURES]
        if save is not None and not isinstance(error, StateError):
            save(state)
    if error is not None:
        error.travel_failure = row
    return row


def validate(rows):
    if not isinstance(rows, list) or len(rows) > MAX_FAILURES:
        raise ValueError("action_failures: expected a bounded list")
    keys = {"at", "scenario", "action", "target", "command", "rc", "reason", "stdout", "stderr", "kind"}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not keys <= row.keys()
            or row.keys() - keys - {"recovered", "session_id", "counts", "quit_status"}
            or row["scenario"] not in (None, "work", "away")
            or any(
                not isinstance(row[k], str) or len(row[k]) > 2000
                for k in keys - {"scenario", "rc", "command"}
            )
            or not isinstance(row["command"], list)
            or len(row["command"]) > 32
            or any(not isinstance(v, str) or len(v) > 512 for v in row["command"])
            or (row["rc"] is not None and type(row["rc"]) is not int)
            or ("recovered" in row and type(row["recovered"]) is not bool)
        ):
            raise ValueError("action_failures: invalid receipt")
        if "session_id" in row and (not isinstance(row["session_id"], str) or len(row["session_id"]) > 2000):
            raise ValueError("action_failures: invalid session id")
        if "counts" in row and (
            not isinstance(row["counts"], dict)
            or set(row["counts"]) != {"attempted", "succeeded", "failed"}
            or any(type(v) is not int or v < 0 for v in row["counts"].values())
        ):
            raise ValueError("action_failures: invalid counts checkpoint")
        datetime.fromisoformat(row["at"])
        if "quit_status" in row and row["quit_status"] not in {"refused", "timeout", "not_running", "error"}:
            raise ValueError("action_failures.quit_status: invalid quit outcome")


def message(row):
    return t(
        "failure.item",
        action=row["action"],
        reason=" ".join(row["reason"].split()).rstrip(".。"),
        path=log_path(),
    )


def session_receipts(state):
    """Recover failed journal checkpoints from the bounded log at report time."""
    existing = state.get("action_failures", [])
    if "session" not in state:
        return list(existing)
    session_id = state["session"]["id"]
    rows = []
    for path in (log_path().with_suffix(".log.1"), log_path()):
        if not path.exists():
            continue
        try:
            for line in path.read_text("utf-8").splitlines():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("failure log: expected an object")
                row.pop("traceback", None)
                validate([row])
                if row.get("session_id") == session_id and row["kind"] != "warning":
                    rows.append(row)
        except (OSError, ValueError) as exc:
            raise StateError(f"{path}: failure receipts: {exc}") from exc
    matched = set()
    for row in existing:
        match = next(
            (
                i
                for i, logged in enumerate(rows)
                if i not in matched
                and all(logged[k] == row[k] for k in ("at", "action", "command", "rc", "reason"))
            ),
            None,
        )
        if match is None:
            rows.append(row)
        else:
            rows[match] = row
            matched.add(match)
    return rows[-MAX_FAILURES:]


def notify(kind, text):
    emit = _reporter.get()
    if emit:
        emit(kind, text)
    else:
        print(text, file=sys.stderr)


@contextmanager
def reporter(emit):
    token = _reporter.set(emit)
    try:
        yield
    finally:
        _reporter.reset(token)


def result(ok, name):
    batch = _batch.get()
    if batch is not None:
        batch["success" if ok else "failed"].append(name)


def warning(state, action, reason, commands=(), *, target=""):
    record(state, action, reason, commands, target=target, kind="warning", store=False)
    values = _batch.get()
    if values is not None:
        values["warnings"] = True
    notify("warn", reason)


@contextmanager
def batch(emit=None):
    values = dict(success=[], failed=[], warnings=False)
    token = _batch.set(values)
    try:
        yield values
    finally:
        _batch.reset(token)
        if values["failed"]:
            text = t(
                "failure.summary",
                n=len(values["success"]),
                m=len(values["failed"]),
                names=", ".join(values["failed"]),
            )
            (emit or notify)("bad", text)
        elif values["warnings"]:
            (emit or notify)("ok", t("failure.summary_success", n=len(values["success"])))


def succeeded(value):
    if isinstance(value, tuple):
        return bool(value[1]) if len(value) == 3 else value[1] == 0
    return bool(value)


def mutation(command):
    bare = [part for part in command if part not in {"sudo", "-n"}]
    if not bare:
        return False
    return (
        bare[0] in {"kill", "open", "osascript"}
        or (bare[0] == "pmset" and bare[1:2] != ["-g"])
        or bare[:2]
        in (
            ["launchctl", "load"],
            ["launchctl", "unload"],
            ["mdutil", "-i"],
            ["networksetup", "-setairportpower"],
        )
        or (bare[0] == "brightness" and bare[1:2] != ["-l"])
        or (bare[0] == "blueutil" and len(bare) == 3)
    )


def action_name(action):
    label = getattr(action, "failure_label", None)
    if label is not None:
        return label
    name = (
        getattr(action, "name", "")
        or getattr(action, "path", "")
        or getattr(getattr(action, "spec", None), "name", "")
    )
    label = action.describe() if getattr(action, "description", None) else type(action).__name__
    return label + (f": {name}" if name else "")


def probe(state, label, function, *args, save, emit):
    """An item's read prerequisite may fail without aborting independent items."""
    from . import sessions
    from .system.shell import ReadError, SudoReadError

    with capture() as commands:
        try:
            return function(*args)
        except (CommandError, ReadError, SudoReadError) as exc:
            sessions.begin(state)
            if "session" in state:
                counts = state["session"]["actions"]
                counts["attempted"] += 1
                counts["failed"] += 1
            row = record(state, label, str(exc), commands)
            save(state)
            result(False, label)
            emit("bad", message(row))
            return None

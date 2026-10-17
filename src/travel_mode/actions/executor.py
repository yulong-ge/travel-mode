"""Ordered action execution, journal scope and same-TUI transaction admission."""

from contextlib import asynccontextmanager, nullcontext
from functools import wraps
from threading import RLock

from ..i18n import t
from ..state import StateError, _path, save_state
from . import journal
from .base import Context


class Executor:
    def __init__(self, context=None, *, count_actions=True):
        self.context = context
        self._lock = RLock()
        self.count_actions = count_actions

    def execute(self, action, context=None):
        ctx = context or self.context or Context(journal.active_state())
        if ctx.state is None and hasattr(action, "state"):
            ctx = Context(action.state, getattr(action, "save", None))
            context = ctx
        if action.requires_journal and ctx.state is None:
            raise StateError(t("safety.journal_required", field=type(action).__name__))
        scope = (
            journal.mutation_scope(ctx.state, ctx.save)
            if self.count_actions
            and (context is not None or self.context is not None)
            and ctx.state is not None
            else nullcontext()
        )
        with self._lock, scope:
            from .. import failures, sessions
            from ..system.shell import ReadError, SudoReadError, is_dry_run

            counted = self.count_actions and ctx.state is not None and not is_dry_run()
            if counted:
                if "session_report_pending" in ctx.state:
                    raise StateError(f"{_path()}: session_report_pending: {t('session.pending_report')}")
                started = False
                try:
                    journal.persist()  # Fail before any mutation if recovery storage is unavailable.
                    sessions.begin(ctx.state)
                    counts = ctx.state["session"]["actions"]
                    counts["attempted"] += 1
                    started = True
                    journal.persist()
                except StateError as exc:
                    if started:
                        counts["failed"] += 1
                    failures.result(False, failures.action_name(action))
                    failures.record(
                        ctx.state, failures.action_name(action), str(exc), error=exc, kind="state"
                    )
                    raise
            with failures.capture() as commands:
                try:
                    result = action.apply(ctx)
                except (failures.CommandError, ReadError, SudoReadError, PermissionError) as exc:
                    if getattr(exc, "fatal", False):
                        raise
                    from .base import Command

                    rc = getattr(exc, "rc", 1)
                    if isinstance(exc, failures.CommandError) and not any(row["rc"] for row in commands):
                        commands.append(dict(command=exc.command, rc=rc, stdout="", stderr=exc.detail))
                    result = (
                        (str(exc), rc)
                        if isinstance(action, Command) or getattr(action, "command_result", False)
                        else (
                            ("", False, str(exc))
                            if type(action).__name__ in {"QuitDaemonApp", "QuitConfiguredDaemons"}
                            else False
                        )
                    )
                    reason = str(exc)
                except Exception as exc:
                    if counted:
                        counts["failed"] += 1
                    failures.result(False, failures.action_name(action))
                    if isinstance(exc, StateError) and callable(getattr(action, "command", None)):
                        intended = (["sudo"] if getattr(action, "sudo", False) else []) + action.command()
                        if not any(row["command"] == intended for row in commands):
                            commands.append(dict(command=intended, rc=None, stdout="", stderr=""))
                    failures.record(
                        ctx.state,
                        failures.action_name(action),
                        str(exc),
                        commands,
                        error=exc,
                        kind="state" if isinstance(exc, StateError) else "internal",
                        target=getattr(action, "path", ""),
                        save=ctx.save if counted else None,
                    )
                    if counted and not isinstance(exc, StateError):
                        journal.persist()
                    raise
                else:
                    reason = (
                        result.explanation(getattr(action, "name", ""))
                        if hasattr(result, "explanation")
                        else str(result[2] if len(result) == 3 else result[0])
                        if isinstance(result, tuple)
                        else t("failure.incomplete")
                    )
            ok = failures.succeeded(result)
            if getattr(result, "command", None):
                commands[:] = [
                    dict(command=result.command, rc=result.rc, stdout=result.stdout, stderr=result.stderr)
                ]
            label = failures.action_name(action)
            target = (
                getattr(action, "path", "")
                or (result[0] if isinstance(result, tuple) and len(result) == 3 else "")
                or ""
            )
            recovered = (
                [r for r in commands if r["rc"] and r["command"][:1] == ["osascript"]]
                if ok and not hasattr(result, "exited")
                else []
            )
            # An exhausted quit strategy is one failed action with both causes.
            # A successful fallback completes both admitted steps successfully.
            extra_steps = getattr(result, "attempts", 1) - 1 + len(recovered) if ok else 0
            if counted:
                counts["attempted"] += extra_steps
                counts["succeeded"] += extra_steps
                counts["succeeded" if ok else "failed"] += 1
                try:
                    journal.persist()
                except StateError as exc:
                    if ok:
                        counts["succeeded"] -= 1
                        counts["failed"] += 1
                    failures.result(False, label)
                    failures.record(
                        ctx.state, label, str(exc), commands, error=exc, kind="state", target=target
                    )
                    raise
            failures.result(ok, label)
            for _ in range(extra_steps):
                failures.result(True, label)
            if ok and (getattr(result, "forced", False) or recovered):
                from pathlib import Path

                warning = (
                    result.explanation(getattr(action, "name", ""))
                    if getattr(result, "forced", False)
                    else t(
                        "failure.forced_refused"
                        if "(-128)" in recovered[-1]["stderr"] + recovered[-1]["stdout"]
                        else "failure.forced_error",
                        name=Path(target).stem,
                    )
                )
                failures.warning(ctx.state, label, warning, commands, target=target)
            if not ok:
                if (
                    commands
                    and not hasattr(result, "explanation")
                    and not (isinstance(result, tuple) and len(result) == 3)
                ):
                    bad = next((row for row in reversed(commands) if row["rc"]), None)
                    if bad:
                        reason = t(
                            "json.command_failed",
                            command=" ".join(bad["command"]),
                            rc=bad["rc"],
                            detail=bad["stderr"] or bad["stdout"],
                        )
                row = failures.record(ctx.state, label, reason, commands, target=target)
                if ctx.state is not None:
                    (ctx.save or save_state)(ctx.state)
                failures.notify("bad", failures.message(row))
            elif type(action).__name__ == "ForceQuitApp" and ctx.state:
                path = getattr(action, "path", "")
                previous = next(
                    (r for r in reversed(ctx.state.get("action_failures", [])) if r["target"] == path), None
                )
                if previous:
                    from pathlib import Path

                    failures.notify(
                        "ok", t("failure.forced", name=Path(path).stem, reason=previous["reason"])
                    )
                    for row in ctx.state.get("action_failures", []):
                        if row["target"] == path and row["kind"] == "action":
                            row["recovered"] = True
                    (ctx.save or save_state)(ctx.state)
            elif ok and ctx.state:
                # A successful explicit retry resolves this action's earlier
                # receipt, while retaining the failed attempt in the report.
                changed = False
                for row in ctx.state.get("action_failures", []):
                    if row["kind"] == "action" and row["action"] == label and row["target"] == target:
                        row["recovered"] = True
                        changed = True
                if changed:
                    (ctx.save or save_state)(ctx.state)
            return result

    def execute_all(self, actions):
        return [self.execute(action) for action in actions]

    @asynccontextmanager
    async def transaction(self, lock, state, merged, save, authenticate=None):
        async with lock:
            if merged and not _path().exists():
                raise StateError(t("impl.snapshot_deleted_externally_possibly_by_off"))
            from ..system.sudo import Credentials, credential_scope

            with credential_scope(Credentials(authenticate)), journal.mutation_scope(state, save):
                yield


_executor = Executor()


def execute(action):
    return _executor.execute(action)


def _sudo_lifecycle(func):
    """Keep sudo credentials alive for one real on/off lifecycle."""

    @wraps(func)
    def wrapped(*args, **kwargs):
        from ..system.sudo import credential_scope

        with credential_scope():
            return func(*args, **kwargs)

    return wrapped

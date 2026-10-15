"""Ordered action execution, journal scope and same-TUI transaction admission."""

from contextlib import asynccontextmanager, nullcontext
from functools import wraps
from threading import RLock

from ..i18n import t
from ..state import StateError, _path
from . import journal
from .base import Context


class Executor:
    def __init__(self, context=None):
        self.context = context
        self._lock = RLock()

    def execute(self, action, context=None):
        ctx = context or self.context or Context(journal.active_state())
        if ctx.state is None and hasattr(action, "state"):
            ctx = Context(action.state, getattr(action, "save", None))
            context = ctx
        if action.requires_journal and ctx.state is None:
            raise StateError(t("safety.journal_required", field=type(action).__name__))
        scope = (
            journal.mutation_scope(ctx.state, ctx.save)
            if (context is not None or self.context is not None) and ctx.state is not None
            else nullcontext()
        )
        with self._lock, scope:
            from .. import sessions
            from ..system.shell import is_dry_run

            counted = ctx.state is not None and not is_dry_run()
            if counted:
                if "session_report_pending" in ctx.state:
                    raise StateError(f"{_path()}: session_report_pending: {t('session.pending_report')}")
                journal.persist()  # Fail before sampling or mutating if recovery storage is unavailable.
                sessions.begin(ctx.state)
                counts = ctx.state["session"]["actions"]
                counts["attempted"] += 1
                journal.persist()
            try:
                result = action.apply(ctx)
            except Exception:
                if counted:
                    counts["failed"] += 1
                    journal.persist()
                raise
            if counted:
                ok = (
                    result[1]
                    if isinstance(result, tuple) and len(result) == 3
                    else result[1] == 0
                    if isinstance(result, tuple)
                    else bool(result)
                )
                counts["succeeded" if ok else "failed"] += 1
                journal.persist()
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

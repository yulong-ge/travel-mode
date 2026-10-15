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
            return action.apply(ctx)

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

"""sudo 凭据的有界生命周期，不清除其它终端的凭据。"""

from __future__ import annotations

import shlex
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event, Thread

from ..i18n import t
from .shell import is_dry_run, run_rc


class SudoKeepAlive:
    def __init__(self, interval: float = 60):
        if interval <= 0:
            raise ValueError(t("sudo.invalid_interval"))
        self.interval = interval
        self.stop = Event()
        self.thread: Thread | None = None
        self.error: RuntimeError | None = None

    def __enter__(self):
        # CLI/TUI explicitly acquire dry-run credentials when a terminal is available.
        # There are no mutations requiring a background refresh in this lifecycle.
        output, rc = run_rc(["-v"], sudo=True, interactive=True)
        if rc:
            raise RuntimeError(t("sudo.failed", operation="sudo -v", rc=rc, detail=output))
        if is_dry_run():
            return self
        self.stop.clear()
        self.thread = Thread(target=self._refresh, name="travel-mode-sudo", daemon=True)
        self.thread.start()
        return self

    def _refresh(self):
        while not self.stop.wait(self.interval):
            output, rc = run_rc(["-n", "-v"], sudo=True)
            if rc:
                self.error = RuntimeError(t("sudo.failed", operation="sudo -n -v", rc=rc, detail=output))
                self.stop.set()

    def check(self):
        if self.error is not None:
            raise self.error

    def __exit__(self, exc_type, exc, traceback):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
            self.thread = None
        self.check()
        return False


class SudoCredentialError(RuntimeError):
    """Credentials could not be obtained before a privileged command."""


class Credentials:
    """Probe the exact command before execution; authenticate only on demand."""

    def __init__(self, authenticate=None):
        self.authenticate = authenticate
        self.keepalive = None

    def ensure(self, command):
        if self.keepalive is not None:
            self.keepalive.check()
            return
        output, rc = run_rc(["sudo", "-n", "-l", *command], timeout=10)
        if rc == 0:
            return
        if not sys.stdin.isatty():
            raise SudoCredentialError(t("sudo.command_required", command=shlex.join(command), detail=output))
        if self.authenticate is None:
            self.keepalive = SudoKeepAlive().__enter__()
        else:
            self.keepalive = self.authenticate()

    def close(self):
        if self.keepalive is not None:
            self.keepalive.__exit__(None, None, None)
            self.keepalive = None


_current = ContextVar("travel_mode_credentials", default=None)


@contextmanager
def credential_scope(credentials=None):
    existing = _current.get()
    if existing is not None:
        yield existing
        return
    credentials = credentials or Credentials()
    token = _current.set(credentials)
    try:
        yield credentials
    finally:
        _current.reset(token)
        credentials.close()


def ensure_command(command):
    credentials = _current.get()
    if credentials is not None:
        credentials.ensure(command)

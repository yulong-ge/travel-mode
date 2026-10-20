"""sudo 凭据的有界生命周期，不清除其它终端的凭据。"""

from __future__ import annotations

import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event, Thread

from ..i18n import t
from .shell import is_dry_run, run_rc


class SudoKeepAlive:
    def __init__(self, interval: float = 60, *, prompt: str | None = None):
        if interval <= 0:
            raise ValueError(t("sudo.invalid_interval"))
        self.interval = interval
        self.prompt = prompt
        self.stop = Event()
        self.thread: Thread | None = None
        self.error: RuntimeError | None = None

    def __enter__(self):
        # CLI/TUI explicitly acquire dry-run credentials when a terminal is available.
        # There are no mutations requiring a background refresh in this lifecycle.
        command = ["-v"] if self.prompt is None else ["-v", "-p", self.prompt]
        output, rc = run_rc(command, sudo=True, interactive=True)
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


class SudoCredentialError(PermissionError):
    """Credentials could not be obtained before a privileged command."""

    rc = 77

    def __init__(self, command):
        self.command = ["sudo", *command]
        self.detail = t("sudo.not_provided")
        super().__init__(self.detail)


class Credentials:
    """Probe the exact command before execution; authenticate only on demand."""

    def __init__(self, authenticate=None, *, interactive=None):
        self.authenticate = authenticate
        self.interactive = interactive
        self.keepalive = None
        self.decisions = {}
        self.attempted_authentication = False
        self.batch_confirmed = False

    def probe(self, command):
        key = tuple(command)
        if key in self.decisions:
            return self.decisions[key]
        output, rc = run_rc(["sudo", "-n", "-l", *command], timeout=10)
        # -l reports permission, not whether authentication is required.
        # Verbose matching entries expose the authenticate tag (sudo(8)).
        allowed = False
        if rc == 0:
            tags = re.findall(r"(?<![A-Z])(NOPASSWD|PASSWD):", output)
            if tags and tags[-1] == "NOPASSWD":
                allowed = True
            else:
                policy, policy_rc = run_rc(["sudo", "-n", "-l", "-l", *command], timeout=10)
                options = re.findall(r"Options:\s*([^\n]+)", policy)
                if policy_rc == 0 and options and "!authenticate" in options[-1]:
                    allowed = True
                else:
                    _, cached_rc = run_rc(["sudo", "-n", "-v"], timeout=10)
                    allowed = cached_rc == 0
        self.decisions[key] = allowed
        return allowed

    def preflight(self, requests):
        if is_dry_run():
            return []
        return [(command, name) for command, name in requests if not self.probe(command)]

    def ensure(self, command):
        if self.keepalive is not None:
            self.keepalive.check()
            return
        if self.probe(command):
            return
        interactive = sys.stdin.isatty() if self.interactive is None else self.interactive
        if interactive and not self.attempted_authentication:
            self.attempted_authentication = True
            try:
                self.keepalive = (
                    self.authenticate() if self.authenticate is not None else SudoKeepAlive().__enter__()
                )
            except (SudoCredentialError, RuntimeError, KeyboardInterrupt) as exc:
                raise SudoCredentialError(command) from exc
            if self.keepalive is not None:
                return
        raise SudoCredentialError(command)

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


def confirm_batch(actions):
    """Resolve all password interaction on the caller before starting workers."""
    credentials = _current.get()
    if credentials is None or credentials.batch_confirmed:
        return
    from ..actions.privilege_plan import action_requests

    needed = credentials.preflight(action_requests(actions))
    if needed:
        try:
            credentials.ensure(needed[0][0])
        except SudoCredentialError:
            # Each unexecuted action must still get its own failure receipt.
            # ensure() will raise again on its worker, without another prompt.
            credentials.attempted_authentication = True

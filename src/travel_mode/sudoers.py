"""Explicit sudoers snippet generation; no automatic policy installation."""

import os
import pwd
import re
from pathlib import Path

from .i18n import t
from .privileges import rules
from .system import shell

TARGET = Path("/etc/sudoers.d/travel-mode")


class SudoersError(RuntimeError):
    """Policy generation, validation or explicit installation failed."""


def username(value=None):
    value = pwd.getpwuid(os.getuid()).pw_name if value is None else value
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", value) or value.upper() == "ALL":
        raise SudoersError(t("sudoers.invalid_user", user=repr(value)))
    return value


def snippet(scope="pmset", user=None):
    user = username(user)
    lines = ["# " + t("sudoers.header"), "# " + t("sudoers.scope_" + scope)]
    for rule in rules(scope):
        lines.append("# " + t(rule.risk))
        args = " " + rule.arguments if rule.arguments is not None else ""
        lines.append(f'"{user}" ALL=(root) NOPASSWD: {rule.path}{args}')
    return "\n".join(lines) + "\n"


def checked(command, *, interactive=False):
    output, rc = shell.run_rc(command, timeout=30, interactive=interactive)
    if rc:
        raise SudoersError(t("sudoers.command_failed", command=" ".join(command), rc=rc, detail=output))
    return output


def exists():
    if TARGET.is_symlink():
        raise SudoersError(t("sudoers.symlink", path=TARGET))
    if TARGET.exists() and not TARGET.is_file():
        raise SudoersError(t("sudoers.not_file", path=TARGET))
    return TARGET.exists()


def status():
    installed = exists()
    content, error = None, None
    if installed:
        try:
            content = TARGET.read_text(encoding="utf-8")
        except PermissionError:
            # This is an exact, non-interactive read, never an authorization prompt.
            output, rc = shell.run_rc(["sudo", "-n", "cat", str(TARGET)], timeout=5)
            if rc:
                error = t("sudoers.content_unreadable", path=TARGET, detail=output)
            else:
                content = output
    probes = []
    for rule in rules("all"):
        command = ["sudo", "-n", "-l", rule.path, *rule.probe]
        output, rc = shell.run_rc(command, timeout=5)
        probes.append(dict(command=command, available=rc == 0, rc=rc, detail=output))
    return dict(path=str(TARGET), installed=installed, content=content, content_error=error, probes=probes)

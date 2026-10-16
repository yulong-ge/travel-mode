"""User-confirmed sudoers changes through the shared Action/Executor boundary."""

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .. import sudoers
from ..i18n import t
from ..system.shell import is_dry_run
from .base import Action


def confirm(key):
    try:
        answer = input(t(key)).strip()
    except EOFError as exc:
        raise sudoers.SudoersError(t("sudoers.cancelled")) from exc
    if answer != "yes":
        raise sudoers.SudoersError(t("sudoers.cancelled"))


def terminal_required():
    if not is_dry_run() and not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise sudoers.SudoersError(t("sudoers.terminal_required"))


@dataclass
class InstallSudoers(Action):
    scope: str = "pmset"
    force: bool = False
    requires_journal = False
    risk = "aggressive"
    description = "sudoers.install"

    def apply(self, ctx):
        terminal_required()
        existing = sudoers.exists()
        if existing and not self.force:
            raise sudoers.SudoersError(t("sudoers.exists", path=sudoers.TARGET))
        content = sudoers.snippet(self.scope)
        with tempfile.TemporaryDirectory(prefix="travel-mode-sudoers-") as folder:
            temporary = Path(folder) / "travel-mode"
            temporary.write_text(content, encoding="utf-8")
            # Validate as the invoking user. Never elevate visudo implicitly.
            sudoers.checked(["visudo", "-c", "-f", str(temporary)])
            print(t("sudoers.destination", path=sudoers.TARGET))
            print(content, end="")
            if not is_dry_run():
                confirm("sudoers.confirm_install")
                if existing:
                    confirm("sudoers.confirm_replace")
                if sudoers.exists() != existing:
                    raise sudoers.SudoersError(t("sudoers.changed", path=sudoers.TARGET))
            sudoers.checked(
                [
                    "sudo",
                    "install",
                    "-m",
                    "0440",
                    "-o",
                    "root",
                    "-g",
                    "wheel",
                    str(temporary),
                    str(sudoers.TARGET),
                ],
                interactive=not is_dry_run(),
            )
        return True


class UninstallSudoers(Action):
    requires_journal = False
    risk = "aggressive"
    description = "sudoers.uninstall"

    def apply(self, ctx):
        terminal_required()
        if not sudoers.exists():
            raise sudoers.SudoersError(t("sudoers.missing", path=sudoers.TARGET))
        print(t("sudoers.destination", path=sudoers.TARGET))
        if not is_dry_run():
            confirm("sudoers.confirm_uninstall")
            sudoers.exists()  # Reject a replaced symlink before invoking sudo.
        sudoers.checked(["sudo", "rm", str(sudoers.TARGET)], interactive=not is_dry_run())
        return True

"""Explicit file-copy installation through the shared Action/Executor."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .. import menubar
from ..i18n import t
from ..system.shell import is_dry_run
from .base import Action


@dataclass
class InstallMenubar(Action):
    force: bool = False
    requires_journal = False
    description = "menubar.install"

    def apply(self, ctx):
        path = menubar.directory() / menubar.FILENAME
        content = menubar.read_plugin(path)
        if content is not None and content != menubar.payload() and not self.force:
            raise menubar.MenubarError(t("menubar.exists", path=path))
        if is_dry_run():
            print(t("menubar.would_copy", path=path))
            return True
        temporary = None
        try:
            # Hidden, same-directory temporary file: SwiftBar cannot run half a copy.
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".travel-mode-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(menubar.payload())
                handle.flush()
                os.fchmod(handle.fileno(), 0o755)
            temporary.replace(path)
        except OSError as exc:
            raise menubar.MenubarError(t("menubar.file_failed", path=path, detail=str(exc))) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(t("menubar.installed", path=path))
        return True


class UninstallMenubar(Action):
    requires_journal = False
    description = "menubar.uninstall"

    def apply(self, ctx):
        path = menubar.directory() / menubar.FILENAME
        content = menubar.read_plugin(path)
        if content is not None and menubar.MARKER not in content.splitlines(keepends=True):
            raise menubar.MenubarError(t("menubar.foreign", path=path))
        if is_dry_run():
            print(t("menubar.would_remove", path=path))
            return True
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise menubar.MenubarError(t("menubar.file_failed", path=path, detail=str(exc))) from exc
        print(t("menubar.removed", path=path))
        return True

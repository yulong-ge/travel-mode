"""Explicit installation and event notification through the shared Executor."""

import os
import plistlib
from dataclasses import dataclass

from .. import automation
from ..i18n import t
from ..system.shell import is_dry_run, run_rc
from .base import Action


def command(arguments):
    output, rc = run_rc(arguments, timeout=10)
    if rc:
        raise automation.AutomationError(
            t("json.command_failed", command=" ".join(arguments), rc=rc, detail=output)
        )


class InstallAutomation(Action):
    requires_journal = False
    description = "automation.install"

    def apply(self, ctx):
        data = automation.agent_plist()
        existing = automation.read_agent()
        loaded, _ = automation.service_status()
        if loaded:
            if existing != data:
                raise automation.AutomationError(t("automation.already_loaded"))
            return True
        if existing is not None and existing != data:
            raise automation.AutomationError(t("automation.already_loaded"))
        payload = plistlib.dumps(data)
        if is_dry_run():
            print(payload.decode())
        else:
            automation.atomic_write(automation.agent_path(), payload)
        command(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(automation.agent_path())])
        return True


class UninstallAutomation(Action):
    requires_journal = False
    description = "automation.uninstall"

    def apply(self, ctx):
        data = automation.read_agent()
        loaded, _ = automation.service_status()
        if loaded:
            if data is None:
                raise automation.AutomationError(t("automation.foreign_plist", path=automation.agent_path()))
            command(["launchctl", "bootout", f"gui/{os.getuid()}/{automation.LABEL}"])
        if data is not None:
            if is_dry_run():
                print(t("automation.would_remove", path=automation.agent_path()))
            else:
                automation.agent_path().unlink()
        return True


@dataclass
class Notify(Action):
    message: str
    requires_journal = False
    description = "automation.notify"

    def apply(self, ctx):
        # AppleScript argv avoids interpolating configuration/error text as code.
        return run_rc(
            [
                "osascript",
                "-e",
                "on run argv",
                "-e",
                'display notification (item 1 of argv) with title "travel-mode"',
                "-e",
                "end run",
                self.message,
            ],
            timeout=5,
        )


class RestoreAutomation(Action):
    requires_journal = False
    description = "automation.restore"

    def apply(self, ctx):
        from ..flows.restore import mode_off

        return mode_off() == 0

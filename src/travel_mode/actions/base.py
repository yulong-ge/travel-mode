"""Action contracts; recovery intent is persisted by the executor journal."""

from dataclasses import dataclass
from typing import ClassVar

from ..i18n import t
from ..system.shell import run_rc


@dataclass
class Context:
    state: dict | None
    save: object = None


class Action:
    risk: ClassVar[str] = "safe"
    description: ClassVar[str]
    requires_journal: ClassVar[bool] = True

    def describe(self):
        return t(self.description, v0=getattr(self, "name", getattr(self, "path", "")), v1="", v2="")

    def apply(self, ctx: Context):
        raise NotImplementedError


class Command(Action):
    sudo = False
    interactive = False

    def command(self):
        raise NotImplementedError

    def apply(self, ctx):
        # shell.prepare_command persists this action's recovery record before
        # invoking any Runner, and clears only confirmed successful commands.
        return run_rc(self.command(), sudo=self.sudo, interactive=self.interactive)


@dataclass
class RecoveryCommand(Command):
    argv: list
    name: str
    sudo: bool = False
    restore_key: str | None = None
    requires_journal = False
    description = "failure.restore"

    @property
    def failure_label(self):
        if self.restore_key is not None:
            from .privilege_plan import restore_name

            return restore_name(self.restore_key)
        return None

    def command(self):
        return self.argv

    def apply(self, ctx):
        return run_rc(self.command(), sudo=self.sudo, timeout=30)

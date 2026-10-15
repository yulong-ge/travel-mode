"""Application actions share exact identity and daemon validation policies."""

from dataclasses import dataclass

from .. import config as config_api
from ..system import apps, daemon_apps, process_control
from . import journal
from .base import Action, Command


def configured_daemon(name):
    return next((spec for spec in config_api.load_config().apps.with_daemon if spec.name == name), None)


@dataclass
class QuitApp(Action):
    path: str
    name: str
    description = "impl.quit_2"

    def apply(self, ctx):
        ok = apps.grace_quit(self.path, self.name)
        if ok and journal.active_state() is not None:
            journal.record_change("quit_apps", self.path)
        return ok


@dataclass
class ForceQuitApp(Action):
    path: str
    description = "impl.force_quit_3"
    risk = "aggressive"

    def apply(self, ctx):
        ok = apps.force_kill(self.path)
        if ok and journal.active_state() is not None:
            journal.record_change("quit_apps", self.path)
        return ok


@dataclass
class FreezeApp(Action):
    path: str
    description = "impl.frozen_2"

    def apply(self, ctx):
        ok = apps.freeze_app(self.path)
        if ok and journal.active_state() is not None:
            journal.record_change("frozen_apps", self.path)
        return ok


@dataclass
class ThawApp(Action):
    path: str
    state: dict
    description = "impl.thawed_3"

    def apply(self, ctx):
        ok = process_control.thaw_app(self.path, self.state)
        if ok and self.path in self.state.get("frozen_apps", []):
            self.state["frozen_apps"].remove(self.path)
            journal.persist()
        return ok


@dataclass
class QuitDaemonApp(Action):
    spec: object
    state: dict
    save: object = None
    description = "impl.quit_the_configured_daemon_app"

    def apply(self, ctx):
        return daemon_apps.quit_with_daemons(self.spec, self.state, self.save)


@dataclass
class QuitConfiguredDaemons(Action):
    state: dict
    save: object = None
    description = "impl.quit_the_configured_daemon_app"

    def apply(self, ctx):
        return daemon_apps.quit_with_configured_daemon(self.state, self.save)


@dataclass
class UnloadAgent(Command):
    path: str
    description = "impl.unload_all"

    def command(self):
        return ["launchctl", "unload", self.path]


@dataclass
class ResumeProcess(Command):
    requires_journal = False
    pid: str
    description = "impl.thaw_these_processes"

    def command(self):
        return ["kill", "-CONT", self.pid]

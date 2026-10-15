"""Power, indexing and display actions."""

from dataclasses import dataclass

from ..system import brightness, processes
from .base import Action, Command


class EnableSleep(Command):
    description = "impl.fix_pmset_disablesleep"
    sudo = True

    def command(self):
        return ["pmset", "-a", "disablesleep", "0"]


@dataclass
class SetPowerProfile(Command):
    values: list
    domain: str = "-b"
    description = "impl.applying_recommended_power_settings"
    sudo = True

    def command(self):
        return ["pmset", self.domain, *self.values]


@dataclass
class KillCaffeinate(Action):
    target: processes.CaffeinateTarget
    requires_journal = False
    description = "impl.terminate"
    risk = "aggressive"

    def apply(self, ctx):
        return processes.terminate_caffeinate(self.target)


class StopSpotlight(Command):
    description = "impl.stop_spotlight_indexing"
    risk = "aggressive"
    sudo = True

    def command(self):
        return ["mdutil", "-i", "off", "/"]


@dataclass
class SetBrightness(Action):
    value: str | float
    description = "impl.changing_brightness_failed"
    risk = "aggressive"

    def apply(self, ctx):
        return brightness.set_brightness(self.value)


class DisplaySleepNow(Command):
    requires_journal = False
    description = "impl.turn_off_the_display_now_pmset"
    risk = "aggressive"

    def command(self):
        return ["pmset", "displaysleepnow"]


class RestoreDefaults(Command):
    requires_journal = False
    description = "impl.requested_factory_power_defaults"
    risk = "aggressive"
    sudo = True

    def command(self):
        return ["pmset", "restoredefaults"]

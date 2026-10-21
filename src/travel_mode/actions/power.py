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
    visible_rows: int | None = None
    description = "impl.applying_recommended_power_settings"
    sudo = True

    @property
    def plan_units(self):
        return self.visible_rows if self.visible_rows is not None else len(self.values) // 2

    def command(self):
        from ..i18n import t

        for key, value in zip(self.values[::2], self.values[1::2], strict=True):
            if key == "tcpkeepalive" and str(value) != "1":
                raise ValueError(t("redesign.keepalive_locked"))
        return ["pmset", self.domain, *self.values]


@dataclass
class KillCaffeinate(Action):
    command_result = True
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
    command_result = True
    value: str | float
    description = "impl.changing_brightness_failed"
    risk = "aggressive"

    def apply(self, ctx):
        from ..failures import CommandError

        try:
            return brightness.set_brightness(self.value)
        except RuntimeError as exc:
            raise CommandError(["brightness", str(self.value)], 1, str(exc)) from exc


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

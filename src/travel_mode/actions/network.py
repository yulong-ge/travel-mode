"""Network switch actions."""

from dataclasses import dataclass

from .base import Command


@dataclass
class WifiOff(Command):
    device: str
    description = "impl.wi_fi_disabled"
    risk = "aggressive"
    sudo = True

    def command(self):
        return ["networksetup", "-setairportpower", self.device, "off"]


class BluetoothOff(Command):
    description = "impl.bluetooth_disabled"
    risk = "aggressive"

    def command(self):
        return ["blueutil", "-p", "0"]

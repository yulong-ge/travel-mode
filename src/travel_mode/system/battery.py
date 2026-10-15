"""On-demand, unprivileged AppleSmartBattery observations; no retained sampler."""

from __future__ import annotations

import re

from ..i18n import t
from .shell import _read

COMMAND = ["ioreg", "-rn", "AppleSmartBattery"]


class BatteryError(ValueError):
    """An ioreg observation lacks valid measurement fields."""


def unavailable(reason):
    return {"available": False, "reason": reason}


def parse(text: str) -> dict:
    if not text.strip():
        return unavailable("no_battery")
    fields = dict(re.findall(r'^\s*[| ]*"(\w+)"\s*=\s*([^\n]+)', text, re.MULTILINE))

    def fail(key, value):
        raise BatteryError(t("battery.invalid", field=key, value=repr(value)))

    def boolean(key):
        value = fields.get(key)
        if value not in {"Yes", "No"}:
            fail(key, value)
        return value == "Yes"

    external, charging = boolean("ExternalConnected"), boolean("IsCharging")
    # Charging systems may omit raw capacities. They cannot measure system drain.
    if external or charging:
        return unavailable("external_power" if external else "charging")

    def integer(key, *, signed=False):
        value = fields.get(key)
        if value is None or not re.fullmatch(r"-?\d+", value):
            fail(key, value)
        number = int(value)
        if signed:
            if not -(2**63) <= number < 2**64:
                fail(key, value)
            if number >= 2**63:
                number -= 2**64
        elif number < 0:
            fail(key, value)
        return number

    voltage = integer("Voltage")
    current = integer("InstantAmperage" if "InstantAmperage" in fields else "Amperage", signed=True)
    remaining, maximum = integer("AppleRawCurrentCapacity"), integer("AppleRawMaxCapacity")
    minutes = integer("TimeRemaining")
    if voltage == 0:
        fail("Voltage", voltage)
    if maximum == 0:
        fail("AppleRawMaxCapacity", maximum)
    if current >= 0:
        return unavailable("not_discharging")
    watts = -voltage * current / 1_000_000
    energy = voltage * remaining / 1_000_000
    return {
        "available": True,
        "reason": None,
        "voltage_mv": voltage,
        "current_ma": current,
        "remaining_mah": remaining,
        "maximum_mah": maximum,
        "external_connected": external,
        "is_charging": charging,
        "ioreg_minutes": None if minutes == 65535 else minutes,
        "watts": watts,
        "remaining_wh": energy,
        "remaining_hours": energy / watts,
    }


def read() -> dict:
    return parse(_read(COMMAND, timeout=10))


def describe(reading):
    if not reading["available"]:
        return t("battery.unavailable", reason=t(f"battery.reason.{reading['reason']}"))
    return t("battery.current", watts=reading["watts"], hours=reading["remaining_hours"])

"""On-demand, unprivileged AppleSmartBattery observations; no retained sampler."""

from __future__ import annotations

import plistlib
from xml.parsers.expat import ExpatError

from ..i18n import t
from .shell import _read

COMMAND = ["ioreg", "-a", "-rn", "AppleSmartBattery"]


class BatteryError(ValueError):
    """An ioreg observation lacks valid measurement fields."""


class IntervalError(BatteryError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(t("battery.interval_invalid", reason=t("measurement.reason." + reason)))


def unavailable(reason):
    return {"available": False, "reason": reason}


def fields(text):
    # ioreg(8): -a archives the selected registry as an XML property list.
    # https://github.com/apple-oss-distributions/IOKitTools/blob/main/ioreg.tproj/ioreg.8
    # Keep the selected node's direct properties; child nodes never shadow it.
    try:
        roots = plistlib.loads(text.encode("utf-8"))
    except (plistlib.InvalidFileException, ValueError, OverflowError, ExpatError) as exc:
        raise BatteryError(t("battery.invalid", field="ioreg structure", value=repr(text))) from exc
    if not isinstance(roots, list) or len(roots) > 1 or any(not isinstance(row, dict) for row in roots):
        _invalid("ioreg structure", roots)
    return roots[0] if roots else None


def _invalid(key, value):
    raise BatteryError(t("battery.invalid", field=key, value=repr(value)))


def _integer(key, value, *, signed=False):
    if type(value) is not int:
        _invalid(key, value)
    number = int(value)
    if signed:
        if not -(2**63) <= number < 2**64:
            _invalid(key, value)
        if number >= 2**63:
            number -= 2**64
    elif number < 0:
        _invalid(key, value)
    return number


def telemetry(properties):
    if "PowerTelemetryData" not in properties:
        return None
    data = properties["PowerTelemetryData"]
    if not isinstance(data, dict):
        _invalid("PowerTelemetryData", data)
    if not ({"AccumulatedSystemLoad", "SystemLoadAccumulatorCount"} & data.keys()):
        return None

    def number(key, signed=False):
        value = _integer("PowerTelemetryData." + key, data.get(key), signed=signed)
        if not signed and value >= 2**64:
            _invalid("PowerTelemetryData." + key, data.get(key))
        return value

    result = dict(
        system_sum_mw=number("AccumulatedSystemLoad"), system_count=number("SystemLoadAccumulatorCount")
    )
    if "SystemLoad" in data:
        result["system_load_mw"] = number("SystemLoad")
    # On recorded hardware the charging accumulator can stay unchanged while
    # the signed discharge accumulator advances. Keep the actual field source.
    for total, count in [
        ("AccumulatedBatteryDischarge", "BatteryDischargeAccumulatorCount"),
        ("AccumulatedBatteryPower", "BatteryPowerAccumulatorCount"),
    ]:
        if {total, count} & data.keys():
            result.update(
                battery_sum_mw=number(total, signed=True), battery_count=number(count), battery_source=total
            )
            break
    return result


def generation(reading):
    data = reading.get("telemetry")
    return data["system_count"] if data is not None else reading.get("update_time")


def validate_telemetry(data):
    required, optional = (
        {"system_sum_mw", "system_count"},
        {"system_load_mw", "battery_sum_mw", "battery_count", "battery_source"},
    )
    if not isinstance(data, dict) or not required <= data.keys() or data.keys() - required - optional:
        raise ValueError("telemetry: invalid accumulator fields")
    for field in ("system_sum_mw", "system_count", "system_load_mw", "battery_count"):
        if field in data and (type(data[field]) is not int or not 0 <= data[field] < 2**64):
            raise ValueError(f"telemetry.{field}: invalid unsigned accumulator")
    if {"battery_sum_mw", "battery_count", "battery_source"} & data.keys():
        if not {"battery_sum_mw", "battery_count", "battery_source"} <= data.keys():
            raise ValueError("telemetry: incomplete battery accumulator")
        if type(data["battery_sum_mw"]) is not int or not -(2**63) <= data["battery_sum_mw"] < 2**63:
            raise ValueError("telemetry.battery_sum_mw: invalid signed accumulator")
        if data["battery_source"] not in ("AccumulatedBatteryDischarge", "AccumulatedBatteryPower"):
            raise ValueError("telemetry.battery_source: invalid accumulator")


def interval(start, end, *, seconds):
    """Average only a monotonic hardware interval; resets/wraps are not guessed."""
    if seconds < 0:
        raise ValueError(t("battery.interval_order_invalid"))
    source = start.get("power_source", "voltage_current")
    if source != end.get("power_source", "voltage_current"):
        raise IntervalError("source_changed")
    first, last = generation(start), generation(end)
    if first is None or last is None:
        raise IntervalError("missing_update_marker")
    # Telemetry and the top-level timestamp update independently. Equal
    # timestamps can contain fresh counters, but an older snapshot cannot end
    # this interval. Keep the baseline until a later observation arrives.
    if source != "voltage_current" and (
        start.get("update_time") is not None
        and end.get("update_time") is not None
        and end["update_time"] < start["update_time"]
    ):
        return None
    if last < first:
        raise IntervalError("counter_reset")
    if last == first:
        # ioreg may expose a new sum before the matching count. No complete
        # accumulator interval exists yet, even if the sum has changed.
        return None
    if seconds == 0:
        return None
    result = {"seconds": seconds, "power_source": source}
    if source == "voltage_current":
        result["average_watts"] = (start["watts"] + end["watts"]) / 2
        return result
    a, b = start["telemetry"], end["telemetry"]
    delta = b["system_sum_mw"] - a["system_sum_mw"]
    if delta < 0:
        raise IntervalError("counter_reset")
    if delta == 0:
        return None
    result.update(average_watts=delta / (last - first) / 1000, accumulator_samples=last - first)
    if "battery_source" not in a or a.get("battery_source") != b.get("battery_source"):
        result["battery_average_reason"] = "unavailable"
    elif b["battery_count"] < a["battery_count"]:
        result["battery_average_reason"] = "counter_reset"
    elif b["battery_count"] == a["battery_count"]:
        result["battery_average_reason"] = "not_updated"
    elif b["battery_sum_mw"] >= a["battery_sum_mw"]:
        result["battery_average_reason"] = "not_discharging"
    else:
        result.update(
            battery_average_watts=-(b["battery_sum_mw"] - a["battery_sum_mw"])
            / (b["battery_count"] - a["battery_count"])
            / 1000,
            battery_accumulator_samples=b["battery_count"] - a["battery_count"],
            battery_power_source="PowerTelemetryData." + b["battery_source"],
        )
    return result


def describe_interval(reason=None):
    """Explain unavailable estimates without treating telemetry as a UI fault."""
    if reason == "counter_reset":
        return t("battery.interval_discontinuous")
    if reason == "source_changed":
        return t("battery.interval_source_changed")
    if reason in (None, "missing_update_marker"):
        return t("battery.interval_waiting")
    raise ValueError(f"battery interval: unknown reason {reason!r}")


def capacity(properties, *, include_maximum=True):
    """Prefer AppleRaw if either key exists; otherwise select BatteryData.

    A selected schema must contain valid requested fields. Nominal/design
    capacity is never a substitute. Session endpoints request remaining mAh only,
    including while connected to AC; power estimates require both capacities.
    """
    current, maximum = "AppleRawCurrentCapacity", "AppleRawMaxCapacity"
    source, prefix = "AppleRaw", ""
    if not ({current, maximum} & properties.keys()):
        if "BatteryData" not in properties:
            _invalid("capacity (AppleRawCurrentCapacity/AppleRawMaxCapacity or BatteryData)", None)
        properties = properties["BatteryData"]
        if not isinstance(properties, dict):
            _invalid("BatteryData", properties)
        current, maximum = "RemainingCapacity", "FullChargeCapacity"
        source, prefix = "BatteryData", "BatteryData."
    result = {
        "capacity_source": source,
        "remaining_mah": _integer(prefix + current, properties.get(current)),
    }
    if include_maximum:
        result["maximum_mah"] = _integer(prefix + maximum, properties.get(maximum))
        if result["maximum_mah"] == 0:
            _invalid(prefix + maximum, properties.get(maximum))
    return result


def parse(text: str, *, endpoint=False) -> dict:
    """Normalize one node; endpoints need status/capacity even on external power."""
    # On macOS, an unmatched -a selection returns empty stdout (recorded fixture).
    if not text.strip():
        return unavailable("no_battery")
    properties = fields(text)
    if properties is None:
        return unavailable("no_battery")

    def boolean(key):
        value = properties.get(key)
        if type(value) is not bool:
            _invalid(key, value)
        return value

    external, charging = boolean("ExternalConnected"), boolean("IsCharging")
    # Charging systems may omit raw capacities. They cannot measure system drain.
    if not endpoint and (external or charging):
        return unavailable("external_power" if external else "charging")

    def integer(key, *, signed=False):
        return _integer(key, properties.get(key), signed=signed)

    voltage = integer("Voltage")
    if voltage == 0:
        _invalid("Voltage", voltage)
    data = telemetry(properties)
    capacities = capacity(properties, include_maximum=not endpoint)
    common = {
        "available": True,
        "reason": None,
        "voltage_mv": voltage,
        "external_connected": external,
        "is_charging": charging,
        "telemetry": data,
        "update_time": integer("UpdateTime") if "UpdateTime" in properties else None,
        **capacities,
    }
    if endpoint:
        return common
    current_field = "Amperage" if "Amperage" in properties else "InstantAmperage"
    current = integer(current_field, signed=True) if data is None or current_field in properties else None
    minutes = integer("TimeRemaining") if "TimeRemaining" in properties else None
    if data is None and current >= 0:
        return unavailable("not_discharging")
    watts = (
        -voltage * current / 1_000_000
        if data is None
        else data["system_load_mw"] / 1000
        if "system_load_mw" in data
        else None
    )
    energy = voltage * capacities["remaining_mah"] / 1_000_000
    return {
        **common,
        "current_ma": current,
        "current_field": current_field if current is not None else None,
        "power_source": "voltage_current" if data is None else "PowerTelemetryData.SystemLoad",
        "ioreg_minutes": None if minutes == 65535 else minutes,
        "watts": watts,
        "remaining_wh": energy,
        "remaining_hours": energy / watts if data is None else None,
    }


def read(*, timeout=10, endpoint=False) -> dict:
    return parse(_read(COMMAND, timeout=timeout), endpoint=endpoint)


def describe(reading):
    if not reading["available"]:
        return t("battery.unavailable", reason=t(f"battery.reason.{reading['reason']}"))
    description = (
        t("battery.latest", watts=reading["watts"], source=reading["power_source"])
        if reading["watts"] is not None
        else t("battery.awaiting")
    )
    if "capacity_source" in reading:
        description += " " + t("battery.capacity_source", source=reading["capacity_source"])
    return description

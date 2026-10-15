"""Finite measurement windows and before/after comparisons, with an injectable clock."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

from .i18n import t
from .system import battery
from .system.shell import _read, is_dry_run


@dataclass(frozen=True)
class Settings:
    seconds: float = 20
    interval: float = 2
    settle_seconds: float = 10


class Clock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class CancellableClock(Clock):
    def __init__(self, cancelled):
        self.cancelled = cancelled

    def sleep(self, seconds):
        if self.cancelled.wait(seconds):
            raise InterruptedError(t("measurement.cancelled"))


def validate_settings(settings):
    for key, value in vars(settings).items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not (0 <= value <= 3600 if key == "settle_seconds" else 0 < value <= 3600)
        ):
            raise ValueError(t("measurement.invalid", field=key))
    if settings.interval < 0.5:
        raise ValueError(t("measurement.invalid", field="interval"))


def macos_estimate():
    raw = _read(["pmset", "-g", "batt"]).strip()
    match = re.search(r"\b(\d+):(\d{2}) remaining\b", raw)
    if match and int(match[2]) >= 60:
        raise battery.BatteryError(t("battery.invalid", field="pmset -g batt", value=raw))
    return {"minutes": int(match[1]) * 60 + int(match[2]) if match else None, "raw": raw}


def measure(settings=None, *, clock=None, progress=None, cancelled=None, first=None):
    settings, clock = settings or Settings(), clock or Clock()
    validate_settings(settings)
    started = clock.monotonic()
    readings = []
    count = math.ceil(settings.seconds / settings.interval)
    for index in range(count):
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError(t("measurement.cancelled"))
        delay = started + index * settings.interval - clock.monotonic()
        if delay > 0:
            clock.sleep(delay)
        reading = first if index == 0 and first is not None else battery.read()
        if not reading["available"]:
            return {**reading, "samples": len(readings), "macos": macos_estimate()}
        readings.append(reading)
        if progress:
            progress(index + 1, count)
    delay = started + settings.seconds - clock.monotonic()
    if delay > 0:
        clock.sleep(delay)
    watts = sum(row["watts"] for row in readings) / len(readings)
    last = readings[-1]
    return {
        "available": True,
        "reason": None,
        "samples": len(readings),
        "seconds": clock.monotonic() - started,
        "average_watts": watts,
        "remaining_wh": last["remaining_wh"],
        "remaining_hours": last["remaining_wh"] / watts,
        "macos": macos_estimate(),
    }


def describe(result):
    if not result["available"]:
        return battery.describe(result)
    minutes = result["macos"]["minutes"]
    return t(
        "measurement.result",
        watts=result["average_watts"],
        hours=result["remaining_hours"],
        macos=t("measurement.unknown") if minutes is None else t("measurement.minutes", minutes=minutes),
    )


def begin(scenario, enabled, settings, emit, *, clock=None, cancelled=None):
    if enabled is False or (enabled is None and scenario != "work"):
        return None
    if is_dry_run():
        emit(t("measurement.dry_run"))
        return None
    emit(t("measurement.before"))
    result = measure(
        settings,
        clock=clock,
        progress=lambda n, total: emit(t("measurement.progress", n=n, total=total)),
        cancelled=cancelled,
    )
    emit(describe(result))
    return result if result["available"] else None


def finish(before, settings, state, save, emit, *, clock=None, cancelled=None):
    if before is None:
        return None
    clock = clock or Clock()
    emit(t("measurement.settling", seconds=settings.settle_seconds))
    clock.sleep(settings.settle_seconds)
    emit(t("measurement.after"))
    after = measure(
        settings,
        clock=clock,
        progress=lambda n, total: emit(t("measurement.progress", n=n, total=total)),
        cancelled=cancelled,
    )
    result = {"before": before, "after": after, "saved_watts": None, "extra_hours": None}
    if after["available"]:
        result["saved_watts"] = before["average_watts"] - after["average_watts"]
        result["extra_hours"] = after["remaining_wh"] * (
            1 / after["average_watts"] - 1 / before["average_watts"]
        )
    state["measurement"] = result
    save(state)
    emit(comparison_description(result))
    return result


def comparison_description(result):
    if not result["after"]["available"]:
        return describe(result["after"])
    return t(
        "measurement.comparison",
        before=result["before"]["average_watts"],
        after=result["after"]["average_watts"],
        saved=result["saved_watts"],
        extra=result["extra_hours"],
    )


def validate_comparison(value):
    def invalid(field):
        raise ValueError(t("measurement.invalid_state", field=field))

    def number(value, field, *, minimum=None):
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or (minimum is not None and value < minimum)
        ):
            invalid(field)

    if not isinstance(value, dict) or set(value) != {"before", "after", "saved_watts", "extra_hours"}:
        invalid("comparison")
    for key in ("before", "after"):
        row = value[key]
        if not isinstance(row, dict) or type(row.get("available")) is not bool:
            invalid(key)
        if key == "before" and not row["available"]:
            invalid("before.available")
        if type(row.get("samples")) is not int or row["samples"] < int(row["available"]):
            invalid(f"{key}.samples")
        macos = row.get("macos")
        if (
            not isinstance(macos, dict)
            or set(macos) != {"raw", "minutes"}
            or not isinstance(macos.get("raw"), str)
        ):
            invalid(f"{key}.macos")
        if macos.get("minutes") is not None and (type(macos["minutes"]) is not int or macos["minutes"] < 0):
            invalid(f"{key}.macos.minutes")
        if row["available"]:
            for field in ("average_watts", "remaining_wh", "remaining_hours", "seconds"):
                number(row.get(field), f"{key}.{field}", minimum=0)
            if row["average_watts"] == 0 or row["seconds"] == 0 or row.get("reason") is not None:
                invalid(key)
        elif row.get("reason") not in {"no_battery", "external_power", "charging", "not_discharging"}:
            invalid(f"{key}.reason")
    for field in ("saved_watts", "extra_hours"):
        if value["after"]["available"]:
            number(value[field], field)
        elif value[field] is not None:
            invalid(field)

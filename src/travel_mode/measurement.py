"""Finite measurement windows and before/after comparisons, with an injectable clock."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

from .i18n import t
from .system import battery
from .system.shell import ReadError, _read, is_dry_run


@dataclass(frozen=True)
class Settings:
    seconds: float = 180
    interval: float = 5
    settle_seconds: float = 10
    updates: int = 2


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
        if key == "updates":
            if type(value) is not int or not 2 <= value <= 720:
                raise ValueError(t("measurement.invalid", field=key))
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not (0 <= value <= 3600 if key == "settle_seconds" else 0 < value <= 3600)
        ):
            raise ValueError(t("measurement.invalid", field=key))
    if settings.interval < 5:
        raise ValueError(t("measurement.invalid", field="interval"))


def macos_estimate():
    raw = _read(["pmset", "-g", "batt"]).strip()
    match = re.search(r"\b(\d+):(\d{2}) remaining\b", raw)
    if match and int(match[2]) >= 60:
        raise battery.BatteryError(t("battery.invalid", field="pmset -g batt", value=raw))
    return {"minutes": int(match[1]) * 60 + int(match[2]) if match else None, "raw": raw}


def on_external_power():
    raw = _read(["pmset", "-g", "ps"], timeout=5)
    if "AC Power" in raw:
        return True
    if "Battery Power" in raw:
        return False
    raise battery.BatteryError(t("battery.invalid", field="pmset -g ps", value=repr(raw)))


def failed(error):
    return dict(available=False, reason="failed", error=str(error), samples=0)


def measure(settings=None, *, clock=None, progress=None, cancelled=None, source_checked=False):
    settings, clock = settings or Settings(), clock or Clock()
    validate_settings(settings)
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError(t("measurement.cancelled"))
    if not source_checked and on_external_power():
        return dict(available=False, reason="external_power", samples=0, macos=macos_estimate())
    started = clock.monotonic()
    previous = None
    previous_at = started
    updates, intervals, polls = [], [], 0
    selected_source = None

    def unavailable(reason):
        return dict(
            available=False,
            reason=reason,
            power_source=selected_source,
            samples=len(updates),
            polls=polls,
            seconds=clock.monotonic() - started,
            required_updates=settings.updates,
            interval_count=len(intervals),
            macos=macos_estimate(),
        )

    if progress:
        progress(0, settings.updates)
    while True:
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError(t("measurement.cancelled"))
        remaining = settings.seconds - (clock.monotonic() - started)
        if remaining <= 0:
            return unavailable("insufficient_samples")
        # Each window acquires its own initial snapshot. A cached pre-action
        # reading must not make pre-action telemetry count as a fresh baseline.
        reading = battery.read(timeout=min(10, remaining))
        polls += 1
        now = clock.monotonic()
        if not reading["available"]:
            return unavailable(reading["reason"])
        selected_source = reading["power_source"]
        if battery.generation(reading) is None:
            return unavailable("missing_update_marker")
        if previous is None:
            # This first registry snapshot may predate the window/action. Wait
            # for a genuine update before accepting a baseline, then an interval.
            previous, previous_at = reading, now
        else:
            try:
                segment = battery.interval(previous, reading, seconds=now - previous_at)
            except battery.IntervalError as exc:
                return unavailable(exc.reason)
            if segment is not None:
                if updates:
                    intervals.append(
                        {
                            **segment,
                            "start_generation": battery.generation(previous),
                            "end_generation": battery.generation(reading),
                        }
                    )
                updates.append(reading)
                previous, previous_at = reading, now
                if progress:
                    progress(len(updates), settings.updates)
                if len(updates) >= settings.updates:
                    break
        delay = min(settings.interval, settings.seconds - (clock.monotonic() - started))
        if delay > 0:
            clock.sleep(delay)
    macos = macos_estimate()
    if "AC Power" in macos["raw"]:
        return unavailable("external_power")
    source = updates[-1]["power_source"]
    weight = "accumulator_samples" if source != "voltage_current" else "seconds"
    watts = sum(row["average_watts"] * row[weight] for row in intervals) / sum(
        row[weight] for row in intervals
    )
    last = updates[-1]
    result = dict(
        available=True,
        reason=None,
        samples=len(updates),
        polls=polls,
        seconds=clock.monotonic() - started,
        covered_seconds=sum(r["seconds"] for r in intervals),
        interval_count=len(intervals),
        intervals=intervals,
        power_source=source,
        average_watts=watts,
        remaining_wh=last["remaining_wh"],
        remaining_hours=last["remaining_wh"] / watts,
        capacity_source=last["capacity_source"],
        macos=macos,
    )
    if all("battery_average_watts" in row for row in intervals):
        result["battery_average_watts"] = sum(
            r["battery_average_watts"] * r["battery_accumulator_samples"] for r in intervals
        ) / sum(r["battery_accumulator_samples"] for r in intervals)
        result["battery_power_source"] = intervals[-1]["battery_power_source"]
    return result


def describe(result):
    if result.get("reason") == "failed":
        return t("measurement.failed", reason=result["error"])
    if not result["available"]:
        if result["reason"] in {
            "insufficient_samples",
            "counter_reset",
            "missing_update_marker",
            "source_changed",
        }:
            return t(
                "measurement.unavailable",
                reason=t("measurement.reason." + result["reason"]),
                n=result["samples"],
                total=result["required_updates"],
                seconds=result["seconds"],
            )
        return battery.describe(result)
    minutes = result["macos"]["minutes"]
    description = t(
        "measurement.window_result" if "power_source" in result else "measurement.result",
        source=result.get("power_source"),
        seconds=result.get("covered_seconds"),
        intervals=result.get("interval_count"),
        wait=result.get("seconds"),
        watts=result["average_watts"],
        hours=result["remaining_hours"],
        macos=t("measurement.unknown") if minutes is None else t("measurement.minutes", minutes=minutes),
    )
    if "capacity_source" in result:
        description += " " + t("battery.capacity_source", source=result["capacity_source"])
    if result.get("interval_count") == 1:
        description += " " + t("measurement.single_interval")
    return description


def begin(scenario, enabled, settings, emit, *, clock=None, cancelled=None):
    if enabled is False or (enabled is None and scenario != "work"):
        return None
    if is_dry_run():
        emit(t("measurement.dry_run"))
        return None
    validate_settings(settings)
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError(t("measurement.cancelled"))
    try:
        if on_external_power():
            emit(t("measurement.ac_skip"))
            return None
        emit(t("measurement.wait", seconds=settings.seconds, updates=settings.updates))
        emit(t("measurement.before"))
        result = measure(
            settings,
            clock=clock,
            progress=lambda n, total: emit(t("measurement.progress", n=n, total=total)),
            cancelled=cancelled,
            source_checked=True,
        )
    except (ReadError, battery.BatteryError) as exc:
        result = failed(exc)
    emit(describe(result))
    return (
        result
        if result["available"]
        or result.get("reason")
        in {"failed", "insufficient_samples", "counter_reset", "missing_update_marker", "source_changed"}
        else None
    )


def finish(before, settings, state, save, emit, *, clock=None, cancelled=None):
    if before is None:
        return None
    validate_settings(settings)
    after = None
    if before["available"]:
        clock = clock or Clock()
        try:
            if on_external_power():
                after = dict(available=False, reason="external_power", samples=0, macos=macos_estimate())
            else:
                emit(t("measurement.settling", seconds=settings.settle_seconds))
                clock.sleep(settings.settle_seconds)
                emit(t("measurement.after"))
                after = measure(
                    settings,
                    clock=clock,
                    progress=lambda n, total: emit(t("measurement.progress", n=n, total=total)),
                    cancelled=cancelled,
                )
        except (ReadError, battery.BatteryError) as exc:
            after = failed(exc)
    result = {"before": before, "after": after, "saved_watts": None, "extra_hours": None}
    if after is not None and after["available"] and before.get("power_source") != after.get("power_source"):
        after = {
            key: value
            for key, value in after.items()
            if key
            not in {"average_watts", "remaining_wh", "remaining_hours", "battery_average_watts", "intervals"}
        }
        after.update(available=False, reason="source_changed", required_updates=settings.updates)
        result["after"] = after
    if after is not None and after["available"]:
        result["saved_watts"] = before["average_watts"] - after["average_watts"]
        result["extra_hours"] = after["remaining_wh"] * (
            1 / after["average_watts"] - 1 / before["average_watts"]
        )
    state["measurement"] = result
    save(state)
    if before["available"]:
        if after["available"]:
            emit(describe(after))
        emit(comparison_description(result))
    return result


def comparison_description(result):
    if not result["before"]["available"]:
        return describe(result["before"])
    if not result["after"]["available"]:
        return describe(result["after"])
    description = t(
        "measurement.comparison",
        before=result["before"]["average_watts"],
        after=result["after"]["average_watts"],
        saved=result["saved_watts"],
        extra=result["extra_hours"],
    )
    sources = [row.get("capacity_source") for row in (result["before"], result["after"])]
    if all(sources):
        source = sources[0] if sources[0] == sources[1] else " -> ".join(sources)
        description += " " + t("battery.capacity_source", source=source)
    if result["before"].get("power_source") is not None:
        description += " " + t("measurement.power_source", source=result["before"]["power_source"])
    if any(row.get("interval_count") == 1 for row in (result["before"], result["after"])):
        description += " " + t("measurement.single_interval")
    return description


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
    if (
        isinstance(value["before"], dict)
        and not value["before"].get("available")
        and value["after"] is not None
    ):
        invalid("after")
    for key in ("before", "after"):
        row = value[key]
        if key == "after" and row is None and not value["before"].get("available"):
            continue
        if not isinstance(row, dict) or type(row.get("available")) is not bool:
            invalid(key)
        if row.get("reason") == "failed":
            if (
                set(row) != {"available", "reason", "error", "samples"}
                or row["available"]
                or type(row["samples"]) is not int
                or row["samples"] != 0
                or not isinstance(row["error"], str)
                or not row["error"]
            ):
                invalid(key)
            continue
        if (
            key == "before"
            and not row["available"]
            and row.get("reason")
            not in {"insufficient_samples", "counter_reset", "missing_update_marker", "source_changed"}
        ):
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
            if "power_source" in row:
                if row["power_source"] not in ("PowerTelemetryData.SystemLoad", "voltage_current"):
                    invalid(f"{key}.power_source")
                if (
                    row["samples"] < 2
                    or type(row.get("interval_count")) is not int
                    or row["interval_count"] < 1
                ):
                    invalid(f"{key}.interval_count")
                if (
                    not isinstance(row.get("intervals"), list)
                    or len(row["intervals"]) != row["interval_count"]
                ):
                    invalid(f"{key}.intervals")
                number(row.get("covered_seconds"), f"{key}.covered_seconds", minimum=0)
                if row["covered_seconds"] == 0:
                    invalid(f"{key}.covered_seconds")
            if "capacity_source" in row and row["capacity_source"] not in ("AppleRaw", "BatteryData"):
                invalid(f"{key}.capacity_source")
            for field in ("average_watts", "remaining_wh", "remaining_hours", "seconds"):
                number(row.get(field), f"{key}.{field}", minimum=0)
            if row["average_watts"] == 0 or row["seconds"] == 0 or row.get("reason") is not None:
                invalid(key)
        elif row.get("reason") not in {
            "no_battery",
            "external_power",
            "charging",
            "not_discharging",
            "insufficient_samples",
            "counter_reset",
            "missing_update_marker",
            "source_changed",
        }:
            invalid(f"{key}.reason")
    for field in ("saved_watts", "extra_hours"):
        if value["after"] is not None and value["after"]["available"]:
            number(value[field], field)
        elif value[field] is not None:
            invalid(field)

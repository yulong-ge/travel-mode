"""Finite menu status with bounded source, telemetry and kernel sleep-time reads."""

import json
import os
import re
import time
from datetime import datetime

from . import __version__
from .i18n import get_language, t
from .state import load_state
from .system.shell import _read

SOURCE_TIMEOUT = 2


class StatusError(RuntimeError):
    """A status observation failed."""


def observation():
    # Do not call capability detection, migration, snapshots, health or discovery.
    raw = _read(["pmset", "-g", "ps"], timeout=SOURCE_TIMEOUT)
    for name, value in (("AC Power", "ac"), ("Battery Power", "battery"), ("UPS Power", "ups")):
        if name in raw:
            source = value
            break
    else:
        raise StatusError(t("automation.unknown_source", detail=raw.strip()))
    battery = dict(percent=None, remaining_minutes=None, charging_state=None)
    for line in raw.splitlines():
        if "InternalBattery" not in line:
            continue
        percent = re.search(r"\b(\d+)%", line)
        if percent is None or not 0 <= int(percent[1]) <= 100:
            raise StatusError(t("menubar.invalid_battery", detail=line.strip()))
        battery["percent"] = int(percent[1])
        tokens = [token.strip() for token in line.split(";")]
        for state in ("discharging", "charging", "charged", "not charging", "finishing charge"):
            if state in tokens:
                battery["charging_state"] = state
                break
        estimate = re.search(r"\b(\d+):(\d{2}) remaining\b", line)
        if estimate:
            if int(estimate[2]) >= 60:
                raise StatusError(t("menubar.invalid_battery", detail=line.strip()))
            battery["remaining_minutes"] = int(estimate[1]) * 60 + int(estimate[2])
    return source, battery


def source():
    return observation()[0]


def data(*, clock=time.monotonic, now=None):
    from .config import load_config

    started = clock()
    show = load_config().menubar.show
    snapshot = load_state()
    power_source, battery = observation()
    from .sleep_readiness import cached, menu_observation

    sleep_ready = menu_observation(
        _read(["pmset", "-g"], timeout=SOURCE_TIMEOUT), snapshot, observation=cached()
    )
    # Keep pmset's volatile estimate for diagnostics, never use it in the menu.
    battery["system_remaining_minutes"] = battery["remaining_minutes"]
    battery.update(remaining_minutes=None, average_watts=None, estimate_source=None, estimate_error=None)
    result = dict(
        version=1,
        sleep_ready=sleep_ready,
        app_version=__version__,
        active=snapshot is not None,
        scenario=(snapshot.get("scenario") or None) if snapshot else None,
        started_at=(
            snapshot.get("session", {}).get("started_at")
            or snapshot.get("anchored_at")
            or snapshot.get("saved_at")
            or None
        )
        if snapshot
        else None,
        power_source=power_source,
        battery=battery,
        language=get_language(),
        source_timeout_seconds=SOURCE_TIMEOUT,
        menubar_show=show,
    )
    result["menubar_hidden"] = menu_hidden(result)
    result["sleep_warning"] = sleep_warning(result)
    result["duration_minutes"] = None
    if result["started_at"]:
        try:
            start = datetime.fromisoformat(result["started_at"]).astimezone()
            seconds = ((now or datetime.now().astimezone()) - start).total_seconds()
            if seconds < 0:
                raise ValueError("session start is in the future")
            result["duration_minutes"] = int(seconds // 60)
        except ValueError as exc:
            raise StatusError(t("menubar.invalid_start", detail=str(exc))) from exc
    active = (snapshot or {}).get("session", {})
    if (
        power_source == "battery"
        and active.get("start", {}).get("telemetry")
        and not active.get("external_seen", True)
    ):
        from .system import battery as battery_api
        from .system.shell import ReadError

        try:
            reading = battery_api.read(timeout=SOURCE_TIMEOUT)
            if not reading["available"]:
                battery["estimate_error"] = t("battery.reason." + reading["reason"])
            elif reading["external_connected"] or reading["is_charging"]:
                battery["estimate_error"] = t("battery.reason.external_power")
            else:
                from .live_power import observe

                battery.update(observe(active, reading, now or datetime.now().astimezone()))
                if battery["average_watts"] is None:
                    battery["estimate_error"] = battery_api.describe_interval()
        except battery_api.IntervalError as exc:
            battery["estimate_error"] = battery_api.describe_interval(exc.reason)
        except (battery_api.BatteryError, ReadError) as exc:
            battery["estimate_error"] = str(exc)
    result["swiftbar"] = render(result, now=now)
    result["elapsed_seconds"] = clock() - started
    return result


def duration(minutes):
    return t("menubar.duration", hours=minutes // 60, minutes=minutes % 60)


def header(symbol, text=""):
    # Embedded symbols avoid NSStatusBarButton's separate image/title spacing.
    # https://github.com/swiftbar/SwiftBar/blob/v2.1.1/README.md#text
    # https://github.com/swiftbar/SwiftBar/blob/v2.1.1/SwiftBar/Utility/NSMutableAttributedString+SFSymbols.swift
    title = f":{symbol}:" + (f" {text}" if text else "")
    return f"{title} | symbolize=true sfsize=16 dropdown=false emojize=false"


def error_menu(detail):
    return "\n".join(
        (
            header("exclamationmark.triangle"),
            "---",
            t("menubar.read_failed"),
            " ".join(str(detail).replace("|", "/").split()),
        )
    )


def power_line(result):
    battery = result.get("battery", {})
    percent = battery.get("percent")
    state = battery.get("charging_state")
    source = result["power_source"]
    if source == "ac":
        # Normalize only the display. JSON keeps pmset's exact state/percentage.
        # A full percentage takes priority even while pmset says finishing charge.
        if percent == 100 or (
            percent is not None and percent >= 99 and state in {"charged", "finishing charge"}
        ):
            return t("menubar.ac_full")
        if state in {"charging", "finishing charge"}:
            return t("menubar.charging_charging") + (f" · {percent}%" if percent is not None else "")
        if state in {"not charging", "charged", "discharging"}:
            return (
                t("menubar.ac_not_charging", percent=percent)
                if percent is not None
                else t("menubar.ac_not_charging_unknown")
            )
        return t("menubar.source_ac") + (f" · {percent}%" if percent is not None else "")
    text = t("menubar.source_" + source)
    if percent is not None:
        text += f" {percent}%"
    if battery.get("remaining_minutes") is not None and battery.get("estimate_source") in {
        "session_telemetry",
        "wake_telemetry",
    }:
        text += " · " + t(
            "menubar.wake_remaining"
            if battery["estimate_source"] == "wake_telemetry"
            else "menubar.session_remaining",
            watts=battery["average_watts"],
            duration=duration(battery["remaining_minutes"]),
        )
    if battery.get("estimate_source") == "session_including_sleep" and battery.get("average_watts"):
        text += " · " + t("menubar.sleep_average", watts=battery["average_watts"])
    return text


def checked_line(readiness, *, now=None):
    """Date the oldest observation supporting the visible warning, in local time."""
    observed_at = readiness.get("observed_at")
    if not observed_at:
        return None
    warnings = [item for item in readiness.get("items", ()) if item["level"] == "red"]
    values = [item.get("observed_at", observed_at) for item in warnings] or [observed_at]
    try:
        readings = [datetime.fromisoformat(value) for value in values]
        if any(value.tzinfo is None for value in readings):
            raise ValueError("check time must include a timezone")
        oldest = min(readings).astimezone()
    except ValueError as exc:
        raise StatusError(f"sleep_readiness.observed_at: {exc}") from exc
    current = (now or datetime.now().astimezone()).astimezone()
    when = oldest.strftime("%H:%M")
    if oldest.date() != current.date():
        when = t("menubar.checked_date", month=oldest.month, day=oldest.day, time=when)
    text = t("menubar.checked", when=when)
    if (current - oldest).total_seconds() > 3600:
        text += t("menubar.checked_stale")
    return text


def sleep_warning(result):
    """A blocked sleep check needs attention only in a session or on battery."""
    return result.get("sleep_ready", {}).get("state") == "blocked" and (
        result["active"] or result["power_source"] == "battery"
    )


def menu_hidden(result):
    return result.get("menubar_show", "always") == "session" and not result["active"]


def render(result, *, now=None):
    executable = os.environ.get("TRAVEL_MODE_BIN", "travel-mode")
    if any(char in executable for char in "\n\r|\x00"):
        raise StatusError(t("menubar.invalid_executable"))
    if menu_hidden(result):
        return ""
    quoted = json.dumps(executable, ensure_ascii=False)

    minutes = result.get("duration_minutes")
    elapsed = f"{minutes // 60}:{minutes % 60:02d}" if minutes is not None else ""
    active = result["active"]
    scenario = result["scenario"]
    symbol = ("moon.fill" if scenario == "away" else "leaf.fill") if active else "leaf"
    warning = sleep_warning(result)
    if warning:
        symbol = "exclamationmark.triangle"
    rows = [header(symbol, elapsed), "---"]
    if active:
        label = t("menubar.session_" + scenario) if scenario else t("menubar.session_unknown")
        rows.append(
            t("menubar.session", label=label, duration=duration(minutes)) if minutes is not None else label
        )
    else:
        rows.append(t("menubar.inactive"))
    rows.append(power_line(result))
    readiness = result.get("sleep_ready", {})
    quiet = not active and result["power_source"] == "ac"
    for item in readiness.get("items", ()) if not quiet else ():
        if item["level"] == "red":
            if (item.get("action") or {}).get("kind") == "sleep":
                rows.append(t("menubar.sleep_disabled"))
                rows.append(t("menubar.sleep_disabled_detail") + " | color=gray")
            else:
                rows.append(" ".join(item["message"].replace("|", "/").split()))
    if quiet and readiness.get("state") == "blocked":
        rows.append(t("menubar.sleep_on_power") + " | color=gray")
    checked = checked_line(readiness, now=now) if not quiet else None
    if checked:
        rows.append(checked)

    # Documented bash/paramN, terminal, refresh and -- submenu protocol:
    # https://github.com/swiftbar/SwiftBar/blob/v2.1.1/README.md#parameters
    def action(key, symbol, arguments, prefix="", *, checked=False):
        params = " ".join(f"param{index}={json.dumps(arg)}" for index, arg in enumerate(arguments, 1))
        rows.append(
            f"{prefix}{t('menubar.' + key)} | sfimage={symbol} bash={quoted} {params} "
            "terminal=true refresh=true" + (" checked=true" if checked else "")
        )

    if warning and any(
        item["level"] == "red" and (item.get("action") or {}).get("kind") == "sleep"
        for item in readiness.get("items", ())
    ):
        action("repair_sleep", "moon.zzz", ("sleep", "repair"))
    rows.append("---")
    if active:
        action("off", "arrow.uturn.backward", ("off",))
    else:
        action("work", "leaf", ("--scenario", "work"))
        action("away", "moon", ("--scenario", "away"))
    rows.append("---")
    for key, symbol in (("check", "stethoscope"), ("power", "bolt"), ("history", "clock")):
        action(key, symbol, (key,))
    rows.extend(("---", t("menubar.more") + " | sfimage=ellipsis.circle"))
    action("automation", "bolt.horizontal.circle", ("automation", "status"), "--")
    action("edit", "slider.horizontal.3", ("config", "edit"), "--")
    session_only = result.get("menubar_show", "always") == "session"
    action(
        "session_only",
        "leaf",
        ("config", "set-menubar", "always" if session_only else "session"),
        "--",
        checked=session_only,
    )
    rows.append(
        "--" + t("menubar.homepage") + " | sfimage=link href=https://github.com/yulong-ge/travel-mode"
    )
    rows.append("--" + t("menubar.version", version=result.get("app_version", __version__)))
    return "\n".join(rows)

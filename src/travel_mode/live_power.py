"""Session and post-wake estimates shared by the open TUI and finite status calls."""

import fcntl
import json
import os
import re
from datetime import datetime

from . import sessions
from .automation import atomic_write
from .state import StateError, _path
from .system import battery
from .system.shell import _read


def sleep_events(text, start, end):
    """Use full Wake (not DarkWake) as the active-use boundary."""
    sleep = wake = None
    for line in text.splitlines():
        match = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+([+-]\d{4})\s+(Sleep|Wake)\s", line)
        if not match:
            continue
        at = datetime.strptime(match[1] + match[2], "%Y-%m-%d %H:%M:%S%z").timestamp()
        if start <= at <= end:
            if match[3] == "Sleep":
                sleep = max(at, sleep) if sleep is not None else at
            else:
                wake = max(at, wake) if wake is not None else at
    return sleep, wake


def estimate(active, reading, now, text, cache):
    result = dict(average_watts=None, remaining_minutes=None, estimate_source=None, estimate_error=None)
    if (
        not reading.get("available")
        or active["external_seen"]
        or (reading["external_connected"] or reading["is_charging"])
    ):
        return result
    start = active["start"]
    if not start.get("telemetry"):
        return result
    at = now.timestamp()
    started = sessions.timestamp(active["started_at"])
    first = dict(
        telemetry=start["telemetry"],
        update_time=start.get("update_time"),
        power_source="PowerTelemetryData.SystemLoad",
    )
    sleep, wake = sleep_events(text, started, at)
    overall = battery.interval(first, reading, seconds=at - started)
    if overall:
        result.update(average_watts=overall["average_watts"], estimate_source="session_telemetry")
    if sleep is not None:
        result["estimate_source"] = "session_including_sleep"
        if wake is None or wake < sleep:
            return result
        key = [active["id"], wake]
        if cache.get("key") != key:
            cache.clear()
            cache.update(key=key, at=at, reading=reading, phase="await_baseline")
            return result
        if cache["phase"] == "await_baseline":
            # The first read after Wake may still be cached from before Sleep.
            # Accept a new generation as baseline, then wait for its interval.
            if battery.generation(reading) != battery.generation(cache["reading"]):
                cache.update(at=at, reading=reading, phase="active")
            return result
        observed = battery.interval(cache["reading"], reading, seconds=at - cache["at"])
        if observed is None:
            return result
        result.update(average_watts=observed["average_watts"], estimate_source="wake_telemetry")
    if result["average_watts"]:
        result["remaining_minutes"] = round(reading["remaining_wh"] / result["average_watts"] * 60)
    return result


def observe(active, reading, now):
    """One bounded log read; an atomic, process-locked wake baseline, no sampler."""
    text = _read(["pmset", "-g", "log"], timeout=2)
    path = _path().parent / "wake-estimate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        cache = {}
        if path.exists():
            try:
                cache = json.loads(path.read_text())
                if not isinstance(cache, dict) or set(cache) != {"key", "at", "reading", "phase"}:
                    raise ValueError("invalid wake baseline")
                if (
                    not isinstance(cache["key"], list)
                    or len(cache["key"]) != 2
                    or not isinstance(cache["key"][0], str)
                    or type(cache["key"][1]) not in (int, float)
                    or type(cache["at"]) not in (int, float)
                ):
                    raise ValueError("invalid wake identity/time")
                if cache["phase"] not in {"await_baseline", "active"}:
                    raise ValueError("invalid wake phase")
                battery.validate_telemetry(cache["reading"]["telemetry"])
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise StateError(f"{path}: wake baseline: {exc}") from exc
        previous = json.dumps(cache, sort_keys=True)
        result = estimate(active, reading, now, text, cache)
        if json.dumps(cache, sort_keys=True) != previous:
            atomic_write(path, json.dumps(cache).encode())
        return result
    finally:
        os.close(fd)

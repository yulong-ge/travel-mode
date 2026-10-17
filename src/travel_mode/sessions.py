"""Local session receipts and finite on-demand reports. No resident sampling."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .i18n import t
from .state import StateError, _path, save_state
from .system.shell import ReadError, _read, is_dry_run


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def history_path():
    return _path().parent / "history.jsonl"


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp: an explicit timezone is required")
    return parsed.astimezone().timestamp()


def observe():
    from .system import battery

    result = dict(
        at=utc_now(),
        external=None,
        charging=None,
        percent=None,
        remaining_mah=None,
        voltage_mv=None,
        errors=[],
    )
    try:
        reading = battery.read(timeout=2, endpoint=True)
        if not reading["available"]:
            result["errors"].append(t("session.no_battery"))
        else:
            result.update(
                external=reading["external_connected"],
                charging=reading["is_charging"],
                remaining_mah=reading["remaining_mah"],
                voltage_mv=reading["voltage_mv"],
            )
            for key in ("telemetry", "update_time"):
                if reading.get(key) is not None:
                    result[key] = reading[key]
    except (ReadError, battery.BatteryError) as exc:
        result["errors"].append(str(exc))
    try:
        output = _read(["pmset", "-g", "batt"], timeout=2)
        match = re.search(r"\b(\d+)%;", output)
        if match and 0 <= int(match[1]) <= 100:
            result["percent"] = int(match[1])
        else:
            result["errors"].append(f"pmset -g batt: {output.strip()!r}")
    except ReadError as exc:
        result["errors"].append(str(exc))
    return result


def begin(state):
    if is_dry_run() or "session" in state:
        return
    start = observe()
    state["session"] = dict(
        id=str(uuid4()),
        started_at=start["at"],
        start=start,
        external_seen=bool(start["external"] or start["charging"]),
        actions=dict(attempted=0, succeeded=0, failed=0),
        restored=0,
        restore_failures=0,
        resurrected=[],
    )


def note(state, rows=(), *, external=False, save=save_state):
    if is_dry_run() or not state or "session" not in state:
        return
    from .state import STATE_LOCK

    with STATE_LOCK:
        active = state["session"]
        new_rows = [row for row in rows if row not in active["resurrected"]]
        if not new_rows and (not external or active["external_seen"]):
            return
        from .state import load_state

        current = load_state()
        if current is None or current.get("session", {}).get("id") != active["id"]:
            raise StateError(f"{_path()}: session.id: {t('session.changed_state')}")
        changed = external and not active["external_seen"]
        active["external_seen"] |= external
        for row in rows:
            if row not in active["resurrected"]:
                active["resurrected"].append(dict(row))
                changed = True
        if changed:
            save(state)


def log_window(text, start, end):
    """Keep exact recorded events; never extrapolate partial sleep segments."""
    selected, sources = [], []
    for line in text.splitlines():
        if not re.match(r"\d{4}-\d\d-\d\d ", line):
            continue
        match = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\s+([+-]\d{4}))?", line)
        if not match:
            raise ValueError(f"pmset log timestamp: {line!r}")
        dt = datetime.strptime(match[1] + (match[2] or ""), "%Y-%m-%d %H:%M:%S" + ("%z" if match[2] else ""))
        epoch = dt.astimezone().timestamp()
        if epoch <= end:
            if re.search(r"Using AC|AC Power|AC attached|AC Attach", line, re.I):
                sources.append((epoch, "ac"))
            elif re.search(r"Using Batt|Battery Power|AC detached|AC Detach", line, re.I):
                sources.append((epoch, "battery"))
        if start <= epoch <= end:
            selected.append(line)
    sources.sort()
    anchors = [source for when, source in sources if when <= start]
    external = (anchors and anchors[-1] == "ac") or any(
        source == "ac" and when >= start for when, source in sources
    )
    coverage = "external_seen" if external else "battery_only" if anchors else "unknown"
    return "\n".join(selected), coverage


def build_report(state, restored, failed):
    from .flows.report import parse_darkwake_reasons, parse_sleep_segments

    active = state.get("session")
    end = observe()
    report = dict(
        version=1,
        id=active["id"] if active else str(uuid4()),
        scenario=state.get("scenario") or None,
        started_at=active["started_at"] if active else None,
        ended_at=end["at"],
        duration_seconds=None,
        start=active["start"] if active else None,
        end=end,
        percent_drop=None,
        mah_drop=None,
        average_watts=None,
        average_scope=None,
        average_source=None,
        power_status="unknown",
        measurement=state.get("measurement"),
        actions=dict(active["actions"]) if active else None,
        restore=dict(
            restored=(active["restored"] if active else 0) + restored,
            failed=failed,
            failed_attempts=(active["restore_failures"] if active else 0) + failed,
        ),
        resurrected=active["resurrected"] if active else [],
        away=None,
        errors=list(end["errors"]),
    )
    from .failures import session_receipts

    receipts = session_receipts(state)
    if receipts or "action_failures" in state:
        report["failures"] = receipts
    if report["actions"] is not None:
        for receipt in receipts:
            counts = receipt.get("counts")
            if counts and (
                counts["attempted"] > report["actions"]["attempted"]
                or (
                    counts["attempted"] == report["actions"]["attempted"]
                    and counts["succeeded"] + counts["failed"]
                    > report["actions"]["succeeded"] + report["actions"]["failed"]
                )
            ):
                report["actions"] = dict(counts)
    if not active:
        report["errors"].append(t("session.missing_start"))
        return report
    seconds = timestamp(end["at"]) - timestamp(active["started_at"])
    if seconds < 0:
        raise StateError(f"{_path()}: session.started_at: end precedes start")
    report["duration_seconds"] = seconds
    start = active["start"]
    report["errors"].extend(start["errors"])
    window = None
    try:
        text = _read(["pmset", "-g", "log"], timeout=5)
        window, report["power_status"] = log_window(text, timestamp(start["at"]), timestamp(end["at"]))
    except (ReadError, ValueError) as exc:
        report["errors"].append(str(exc))
    measurement_external = (
        report["measurement"]
        and report["measurement"]["after"]
        and report["measurement"]["after"].get("reason")
        in {
            "external_power",
            "charging",
        }
    )
    if active["external_seen"] or end["external"] or end["charging"] or measurement_external:
        report["power_status"] = "external_seen"
    values = [start[k] is not None and end[k] is not None for k in ("percent", "remaining_mah", "voltage_mv")]
    if report["power_status"] == "battery_only" and all(values) and not start["errors"] and not end["errors"]:
        percent = start["percent"] - end["percent"]
        mah = start["remaining_mah"] - end["remaining_mah"]
        if min(percent, mah) >= 0:
            report.update(percent_drop=percent, mah_drop=mah)
            if seconds > 0 and "telemetry" not in start and "telemetry" not in end:
                report["average_watts"] = (
                    mah * (start["voltage_mv"] + end["voltage_mv"]) / 2 / 1_000_000 / (seconds / 3600)
                )
                report["average_scope"] = "session"
                report["average_source"] = "capacity_delta"
        else:
            report["power_status"] = "unknown"
    if report["power_status"] == "battery_only" and "telemetry" in start and "telemetry" in end:
        from .system.battery import IntervalError, interval

        try:
            aggregate = interval(
                {"telemetry": start["telemetry"], "power_source": "PowerTelemetryData.SystemLoad"},
                {"telemetry": end["telemetry"], "power_source": "PowerTelemetryData.SystemLoad"},
                seconds=seconds,
            )
            if aggregate is not None:
                report.update(
                    average_watts=aggregate["average_watts"],
                    average_scope="session",
                    average_source=aggregate["power_source"],
                )
            else:
                report["errors"].append(t("measurement.reason.insufficient_samples"))
        except IntervalError as exc:
            report["errors"].append(str(exc))
    if (
        report["average_watts"] is None
        and report["measurement"]
        and report["measurement"]["after"] is not None
        and report["measurement"]["after"]["available"]
    ):
        report["average_watts"] = report["measurement"]["after"]["average_watts"]
        report["average_scope"] = "after_window"
        report["average_source"] = report["measurement"]["after"].get("power_source")
    if report["scenario"] == "away" and window is not None:
        segments = parse_sleep_segments(window)
        reasons = parse_darkwake_reasons(window)
        report["away"] = dict(
            segments=segments,
            percent_drop=sum(r["drain"] for r in segments)
            if segments and report["power_status"] == "battery_only"
            else None,
            darkwake_count=sum(reasons.values()),
            reasons=reasons,
        )
    return report


def validate_active(active):
    if not isinstance(active, dict) or set(active) != {
        "id",
        "started_at",
        "start",
        "external_seen",
        "actions",
        "restored",
        "restore_failures",
        "resurrected",
    }:
        raise ValueError("session: invalid fields")
    if not isinstance(active["id"], str) or not active["id"] or type(active["external_seen"]) is not bool:
        raise ValueError("session: invalid identity/source")
    timestamp(active["started_at"])
    validate_observation(active["start"])
    if (
        not isinstance(active["actions"], dict)
        or set(active["actions"]) != {"attempted", "succeeded", "failed"}
        or any(type(v) is not int or v < 0 for v in active["actions"].values())
    ):
        raise ValueError("session.actions: invalid counts")
    for key in ["restored", "restore_failures"]:
        if type(active[key]) is not int or active[key] < 0:
            raise ValueError(f"session.{key}: invalid count")
    if not isinstance(active["resurrected"], list) or any(
        not isinstance(row, dict) for row in active["resurrected"]
    ):
        raise ValueError("session.resurrected: invalid observations")


def validate_observation(row):
    required = {
        "at",
        "external",
        "charging",
        "percent",
        "remaining_mah",
        "voltage_mv",
        "errors",
    }
    if (
        not isinstance(row, dict)
        or not required <= row.keys()
        or row.keys() - required - {"telemetry", "capacity_source", "update_time"}
    ):
        raise ValueError("observation: invalid fields")
    if "telemetry" in row:
        from .system.battery import validate_telemetry

        validate_telemetry(row["telemetry"])
    if "capacity_source" in row and row["capacity_source"] not in ("AppleRaw", "BatteryData"):
        raise ValueError("observation.capacity_source: invalid source")
    if "update_time" in row and (type(row["update_time"]) is not int or row["update_time"] < 0):
        raise ValueError("observation.update_time: invalid timestamp")
    timestamp(row["at"])
    for key in ("external", "charging"):
        if row[key] is not None and type(row[key]) is not bool:
            raise ValueError(f"observation.{key}: expected boolean or null")
    for key in ("percent", "remaining_mah", "voltage_mv"):
        if row[key] is not None and (type(row[key]) is not int or row[key] < 0):
            raise ValueError(f"observation.{key}: expected nonnegative integer or null")
    if row["percent"] is not None and row["percent"] > 100:
        raise ValueError("observation.percent: exceeds 100")
    if not isinstance(row["errors"], list) or any(not isinstance(v, str) for v in row["errors"]):
        raise ValueError("observation.errors: expected strings")


def validate_report(row):
    required = {
        "version",
        "id",
        "scenario",
        "started_at",
        "ended_at",
        "duration_seconds",
        "start",
        "end",
        "percent_drop",
        "mah_drop",
        "average_watts",
        "average_scope",
        "power_status",
        "measurement",
        "actions",
        "restore",
        "resurrected",
        "away",
        "errors",
    }
    if (
        not isinstance(row, dict)
        or not required <= row.keys()
        or row.keys() - required - {"average_source", "failures"}
        or row["version"] != 1
    ):
        raise ValueError("report: invalid fields/version")
    if "failures" in row:
        from .failures import validate

        validate(row["failures"])
    if row.get("average_source") not in (
        None,
        "PowerTelemetryData.SystemLoad",
        "voltage_current",
        "capacity_delta",
    ):
        raise ValueError("report.average_source: invalid source")
    if not isinstance(row["id"], str) or not row["id"] or row["scenario"] not in ("work", "away", None):
        raise ValueError("report: invalid identity/scenario")
    timestamp(row["ended_at"])
    if row["started_at"] is not None:
        timestamp(row["started_at"])
    for key in ("duration_seconds", "percent_drop", "mah_drop", "average_watts"):
        v = row[key]
        if v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0):
            raise ValueError(f"report.{key}: invalid number")
    if row["average_scope"] not in ("session", "after_window", None) or row["power_status"] not in (
        "battery_only",
        "external_seen",
        "unknown",
    ):
        raise ValueError("report: invalid power classification")
    validate_observation(row["end"])
    if row["start"] is not None:
        validate_observation(row["start"])
    if row["started_at"] is None and (row["start"] is not None or row["duration_seconds"] is not None):
        raise ValueError("report.start: inconsistent missing start")
    if (row["average_watts"] is None) != (row["average_scope"] is None):
        raise ValueError("report.average_scope: inconsistent average")
    if row["average_scope"] == "session" and row["power_status"] != "battery_only":
        raise ValueError("report.average_watts: charging interval cannot be averaged")
    if not isinstance(row["errors"], list) or any(not isinstance(v, str) for v in row["errors"]):
        raise ValueError("report.errors: expected strings")
    if (
        not isinstance(row["restore"], dict)
        or set(row["restore"]) != {"restored", "failed", "failed_attempts"}
        or any(type(v) is not int or v < 0 for v in row["restore"].values())
    ):
        raise ValueError("report.restore: invalid counts")
    if row["actions"] is not None:
        counts = row["actions"]
        if (
            not isinstance(counts, dict)
            or set(counts) != {"attempted", "succeeded", "failed"}
            or any(type(v) is not int or v < 0 for v in counts.values())
        ):
            raise ValueError("report.actions: invalid counts")
    if row["measurement"] is not None:
        from .measurement import validate_comparison

        validate_comparison(row["measurement"])
    if not isinstance(row["resurrected"], list) or any(
        not isinstance(v, dict)
        or not {"kind", "path", "name"} <= set(v)
        or v["kind"] not in ("app", "agent", "pid")
        or any(not isinstance(v[k], str) for k in ("path", "name"))
        for v in row["resurrected"]
    ):
        raise ValueError("report.resurrected: invalid observations")
    if row["away"] is not None:
        value = row["away"]
        if (
            not isinstance(value, dict)
            or set(value) != {"segments", "percent_drop", "darkwake_count", "reasons"}
            or not isinstance(value["segments"], list)
            or type(value["darkwake_count"]) is not int
            or value["darkwake_count"] < 0
            or not isinstance(value["reasons"], dict)
            or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in value["reasons"].items())
        ):
            raise ValueError("report.away: invalid sleep observations")
        if value["percent_drop"] is not None and (
            type(value["percent_drop"]) not in (int, float)
            or not math.isfinite(value["percent_drop"])
            or value["percent_drop"] < 0
        ):
            raise ValueError("report.away.percent_drop: invalid discharge")
        for segment in value["segments"]:
            if (
                not isinstance(segment, dict)
                or set(segment) != {"start", "end", "start_charge", "end_charge", "drain"}
                or any(
                    type(segment[k]) is not int or segment[k] < 0
                    for k in ("start_charge", "end_charge", "drain")
                )
            ):
                raise ValueError("report.away.segments: invalid segment")


def read_history(path=None):
    target = Path(path) if path else history_path()
    if not target.exists():
        return []
    try:
        rows = []
        for index, line in enumerate(target.read_text("utf-8").splitlines(), 1):
            try:
                row = json.loads(line)
                validate_report(row)
            except (ValueError, TypeError, KeyError) as exc:
                raise StateError(f"{target}: line {index}: {exc}") from exc
            if any(r["id"] == row["id"] for r in rows):
                raise StateError(f"{target}: line {index}: duplicate session id")
            rows.append(row)
        return rows
    except (OSError, UnicodeError) as exc:
        raise StateError(f"{target}: read: {exc}") from exc


def append_history(report, retain, path=None):
    target = Path(path) if path else history_path()
    validate_report(report)
    rows = read_history(target)
    existing = next((r for r in rows if r["id"] == report["id"]), None)
    if existing is not None:
        if existing != report:
            raise StateError(f"{target}: id {report['id']}: conflicting report")
        return
    payload = "".join(
        json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in [*rows, report][-retain:]
    )
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    except OSError as exc:
        raise StateError(f"{target}: write: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def comparison(report, rows):
    peers = [
        r["average_watts"]
        for r in rows
        if r["id"] != report["id"]
        and r["scenario"] == report["scenario"]
        and r["average_scope"] == report["average_scope"]
        and r.get("average_source") == report.get("average_source")
        and r["average_watts"] is not None
    ]
    if report["average_watts"] is None or not peers:
        return None
    mean = sum(peers) / len(peers)
    return dict(count=len(peers), average_watts=mean, difference_watts=mean - report["average_watts"])


def finish(state, restored, failed):
    if is_dry_run():
        return None
    from .config import load_config

    if "session_report_pending" not in state:
        state["session_report_pending"] = build_report(state, restored, failed)
        save_state(state)
    report = state["session_report_pending"]
    append_history(report, load_config().history_retain)
    return report


def describe(report, peers=()):
    unknown = t("session.unknown")
    yield t("session.heading", scenario=report["scenario"] or unknown)
    yield t(
        "session.times",
        start=report["started_at"] or unknown,
        end=report["ended_at"],
        seconds=report["duration_seconds"] if report["duration_seconds"] is not None else unknown,
    )
    start = report["start"] or {}
    end = report["end"]
    yield t(
        "session.readings",
        start_percent=start.get("percent") if start.get("percent") is not None else unknown,
        start_mah=start.get("remaining_mah") if start.get("remaining_mah") is not None else unknown,
        end_percent=end["percent"] if end["percent"] is not None else unknown,
        end_mah=end["remaining_mah"] if end["remaining_mah"] is not None else unknown,
    )
    if report["power_status"] != "battery_only":
        yield t(
            "session.external" if report["power_status"] == "external_seen" else "session.coverage_unknown"
        )
    if report["percent_drop"] is not None:
        yield t("session.drain", percent=report["percent_drop"], mah=report["mah_drop"])
    if report["average_watts"] is not None:
        yield t(
            "session.average",
            watts=report["average_watts"],
            scope=t("session.scope.telemetry")
            if report.get("average_source") == "PowerTelemetryData.SystemLoad"
            and report["average_scope"] == "session"
            else t("session.scope." + report["average_scope"]),
        )
        if report.get("average_source") is not None:
            yield t("measurement.power_source", source=report["average_source"])
    else:
        yield t("session.no_average")
    if report["measurement"]:
        from .measurement import comparison_description
        from .measurement import describe as measurement_description

        if report["measurement"]["before"]["available"] and (
            "power_source" in report["measurement"]["before"]
            or not report["measurement"]["after"]["available"]
        ):
            yield t("session.before_window") + ": " + measurement_description(report["measurement"]["before"])
        if (
            report["measurement"]["after"] is not None
            and report["measurement"]["after"]["available"]
            and "power_source" in report["measurement"]["after"]
        ):
            yield t("session.after_window") + ": " + measurement_description(report["measurement"]["after"])
        yield comparison_description(report["measurement"])
    counts = report["actions"]
    yield t(
        "session.actions",
        attempted=counts["attempted"] if counts else unknown,
        succeeded=counts["succeeded"] if counts else unknown,
        failed=counts["failed"] if counts else unknown,
    )
    yield t("session.restore", **report["restore"])
    for failure in report.get("failures", []):
        yield t(
            "failure.history_recovered" if failure.get("recovered") else "failure.history",
            action=failure["action"],
            reason=failure["reason"],
            rc=failure["rc"] if failure["rc"] is not None else unknown,
        )
        if failure["command"]:
            import shlex

            yield t("failure.history_command", command=shlex.join(failure["command"]))
    for row in report["resurrected"]:
        from .resurrection import describe as resurrection_description

        yield resurrection_description(row)
    if report["away"]:
        away = report["away"]
        yield t(
            "session.away",
            percent=away["percent_drop"] if away["percent_drop"] is not None else unknown,
            wakes=away["darkwake_count"],
            reasons=", ".join(f"{key} ({value})" for key, value in away["reasons"].items()) or unknown,
        )
    comp = comparison(report, peers)
    if comp:
        yield t(
            "session.compare" if comp["difference_watts"] >= 0 else "session.compare_higher",
            scenario=report["scenario"] or unknown,
            difference=abs(comp["difference_watts"]),
            count=comp["count"],
        )
    for error in report["errors"]:
        yield t("session.read_error", reason=error)

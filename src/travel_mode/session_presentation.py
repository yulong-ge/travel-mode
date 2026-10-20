"""Pure, localized session presentation. Stored receipts remain unchanged."""

from datetime import datetime
from pathlib import Path

from .i18n import catalog, t


def duration(seconds):
    if seconds is None:
        return t("session.unknown")
    minutes = int(seconds) // 60
    if not minutes:
        return t("report.less_minute")
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return " ".join(
        t(f"report.duration.{unit}." + ("one" if value == 1 else "many"), n=value)
        for unit, value in (("day", days), ("hour", hours), ("minute", minutes))
        if value
    )


def time_range(report):
    end = datetime.fromisoformat(report["ended_at"]).astimezone()
    start = datetime.fromisoformat(report["started_at"]).astimezone() if report["started_at"] else None
    show_year = start is not None and start.year != end.year

    def date(value):
        if value is None:
            return t("session.unknown")
        return t(
            "report.date_year" if show_year else "report.date",
            year=value.year,
            month=t("report.months").split("|")[value.month - 1],
            day=value.day,
            time=value.strftime("%H:%M"),
        )

    if start is None:
        return t("report.no_start", end=date(end))
    return t("report.range", start=date(start), end=date(end), duration=duration(report["duration_seconds"]))


def failure_view(row, report):
    command = [p for p in row["command"] if p not in {"sudo", "-n"}]
    tool = Path(command[0]).name if command else ""
    restoring = row["target"].startswith("restore:") or any(
        row["action"].startswith(catalog(language)["failure.restore"]) for language in ("zh", "en")
    )
    names = {
        "mdutil": "admin.item_spotlight",
        "networksetup": "admin.item_wifi",
        "blueutil": "admin.item_bluetooth",
        "brightness": "admin.item_brightness",
    }
    if tool in names:
        name = t(names[tool])
        if tool == "mdutil" and command[1:3] == ["-i", "on"]:
            restoring = True
    elif row["target"].endswith(".app"):
        name = Path(row["target"]).stem
    elif row["target"].startswith("restore:"):
        from .actions.privilege_plan import restore_name

        name = restore_name(row["target"].removeprefix("restore:"), pending=True)
    else:
        name = row["action"].split(":", 1)[-1].strip()
    evidence = row["reason"] + row["stderr"] + row["stdout"]
    password = (
        row["rc"] == 77
        or row["kind"] == "not_executed"
        or any(
            catalog(language)["report.legacy.password"].casefold() in evidence.casefold()
            for language in ("zh", "en")
        )
    )
    if password:
        reason = "password"
    elif (
        row.get("quit_status") == "refused"
        or "-128" in evidence
        or any(
            catalog(language)["report.legacy.refused"].casefold() in evidence.casefold()
            for language in ("zh", "en")
        )
    ):
        reason = "refused"
    elif row["rc"] == 124 or "timeout" in evidence.lower() or row.get("quit_status") == "timeout":
        reason = "unfinished"
    else:
        reason = "failed"
    return dict(
        name=name,
        reason=reason,
        restoring=restoring,
        recovered=bool(row.get("recovered")) or (restoring and report["restore"]["failed"] == 0),
    )


def describe(report, peers=(), *, verbose=False):
    from .sessions import comparison

    unknown = t("session.unknown")
    yield t(
        "session.heading", scenario=t("scenario." + report["scenario"]) if report["scenario"] else unknown
    )
    yield time_range(report)
    if report.get("appended_scenarios"):
        yield t(
            "conflict.appended",
            scenarios=t("admin.separator").join(t("scenario." + s) for s in report["appended_scenarios"]),
        )
    start, end = report["start"] or {}, report["end"]
    readings = (
        t("report.battery", start=start["percent"], end=end["percent"])
        if start.get("percent") is not None and end["percent"] is not None
        else t("report.battery_unknown")
    )
    if report["power_status"] != "battery_only":
        readings += t(
            "report.external" if report["power_status"] == "external_seen" else "report.unknown_power"
        )
    yield readings.rstrip()
    if report["power_status"] == "battery_only":
        if report["percent_drop"] is not None:
            yield t("report.discharge", percent=report["percent_drop"])
        if report["average_watts"] is not None:
            yield t(
                "session.average",
                watts=report["average_watts"],
                scope=t(
                    "session.scope."
                    + (
                        "telemetry"
                        if report.get("average_source") == "PowerTelemetryData.SystemLoad"
                        and report["average_scope"] == "session"
                        else report["average_scope"]
                    )
                ),
            )
        else:
            yield t("session.no_average")
    measurement = report["measurement"]
    if measurement and measurement.get("mode") == "passive":
        from .measurement import comparison_description

        yield comparison_description(measurement)
    elif measurement:
        before, after = measurement["before"], measurement["after"]
        if before["available"]:
            yield t("report.before", watts=before["average_watts"])
        if after and after["available"]:
            yield t("report.after", watts=after["average_watts"])
        if measurement.get("saved_watts") is not None:
            yield t("report.saving", watts=measurement["saved_watts"])
        if not before["available"] or (after and not after["available"]):
            yield t("report.measure_failed")
    counts = report["actions"]
    yield t("report.actions", **counts) if counts else t("report.actions_unknown")
    failures = [failure_view(row, report) for row in report.get("failures", []) if row["kind"] != "warning"]
    for row in failures:
        if not row["restoring"]:
            yield t("report.failed_item", name=row["name"], reason=t("report.reason." + row["reason"])) + (
                t("report.resolved") if row["recovered"] else ""
            )
    restore = report["restore"]
    yield t("report.restore_complete" if not restore["failed"] else "report.restore_pending", **restore)
    seen = set()
    for row in failures:
        if not row["restoring"] or (row["name"], row["recovered"]) in seen:
            continue
        seen.add((row["name"], row["recovered"]))
        yield t(
            "report.retry_recovered" if row["recovered"] else "report.failed_item",
            name=row["name"],
            reason=t("report.first." + row["reason"])
            if row["recovered"]
            else t("report.reason." + row["reason"]),
        )
    names = list(dict.fromkeys(row["name"] for row in report["resurrected"]))
    if names:
        yield t("report.resurrected", names=t("admin.separator").join(names))
    if report["away"]:
        away = report["away"]
        yield t(
            "session.away",
            percent=away["percent_drop"] if away["percent_drop"] is not None else unknown,
            wakes=away["darkwake_count"],
            reasons=", ".join(f"{k} ({v})" for k, v in away["reasons"].items()) or unknown,
        )
    comp = comparison(report, peers)
    if comp:
        yield t(
            "session.compare" if comp["difference_watts"] >= 0 else "session.compare_higher",
            scenario=t("scenario." + report["scenario"]) if report["scenario"] else unknown,
            difference=abs(comp["difference_watts"]),
            count=comp["count"],
        )
    if report["errors"]:
        yield t("report.read_unavailable")
    if verbose:
        yield from diagnostics(report)


def diagnostics(report):
    import shlex

    yield t("report.details")
    yield t(
        "session.times",
        start=report["started_at"],
        end=report["ended_at"],
        seconds=report["duration_seconds"],
    )
    start, end = report["start"] or {}, report["end"]
    yield t(
        "session.readings",
        start_percent=start.get("percent"),
        end_percent=end["percent"],
        start_mah=start.get("remaining_mah"),
        end_mah=end["remaining_mah"],
    )
    yield t("session.restore", **report["restore"])
    if report["measurement"]:
        import json

        from .measurement import comparison_description

        yield comparison_description(report["measurement"])
        yield t("report.raw_measurement", value=json.dumps(report["measurement"], ensure_ascii=False))
    if report.get("average_source"):
        yield t("measurement.power_source", source=report["average_source"])
    for row in report.get("failures", []):
        yield t(
            "report.raw_failure",
            action=row["action"],
            rc=row["rc"],
            reason=row["reason"],
            stdout=row["stdout"],
            stderr=row["stderr"],
        )
        if row["command"]:
            yield t("failure.history_command", command=shlex.join(row["command"]))
    for error in report["errors"]:
        yield t("session.read_error", reason=error)

"""Pure session content, using the same accumulator interval as status."""

from datetime import datetime

from travel_mode.i18n import t
from travel_mode.status import duration
from travel_mode.system.battery import IntervalError, describe_interval, interval


def session_view(state, reading=None, *, now=None, reading_at=None):
    active = state.get("session")
    result = dict(title="", lines=[], average_watts=None, remaining_minutes=None, percent_change=None)
    if not active:
        return result
    started_at = datetime.fromisoformat(active["started_at"])
    seconds = max(0, ((now or datetime.now().astimezone()) - started_at).total_seconds())
    minutes = int(seconds // 60)
    result["title"] = t(
        "redesign.session_title",
        scenario=t("scenario." + (state.get("scenario") or "work")),
        duration=duration(minutes),
    )
    start = active["start"]
    # Session observations persist whole seconds. A read begun in that same
    # second may still precede the endpoint; wait rather than guess its order.
    stale = reading_at is not None and reading_at.replace(microsecond=0) <= started_at
    if reading and start.get("update_time") is not None and reading.get("update_time") is not None:
        stale |= reading["update_time"] < start["update_time"]
    estimate_message = describe_interval()
    if (
        reading
        and reading.get("available")
        and not active["external_seen"]
        and not reading["external_connected"]
        and not reading["is_charging"]
        and not stale
    ):
        current = (
            round(reading["remaining_mah"] / reading["maximum_mah"] * 100)
            if reading.get("maximum_mah")
            else None
        )
        if current is not None and start["percent"] is not None:
            result["percent_change"] = start["percent"] - current
            result["lines"].append(
                t(
                    "redesign.session_battery",
                    start=start["percent"],
                    current=current,
                    delta=result["percent_change"],
                )
            )
        if start.get("telemetry"):
            try:
                observed = interval(
                    dict(
                        telemetry=start["telemetry"],
                        update_time=start.get("update_time"),
                        power_source="PowerTelemetryData.SystemLoad",
                    ),
                    reading,
                    seconds=seconds,
                )
            except IntervalError as exc:
                observed = None
                estimate_message = describe_interval(exc.reason)
            if observed:
                watts = observed["average_watts"]
                remaining = round(reading["remaining_wh"] / watts * 60)
                result.update(average_watts=watts, remaining_minutes=remaining)
                result["lines"].append(t("redesign.session_power", watts=watts, duration=duration(remaining)))
        if result["average_watts"] is None:
            result["lines"].append(estimate_message)
    elif stale or reading is None:
        result["lines"].append(estimate_message)
    else:
        result["lines"].append(t("redesign.session_incomplete"))
    power = sum(len(str(state.get(key, "")).split()) // 2 for key in ("batt_prof", "ac_prof"))
    result["lines"].append(
        t(
            "redesign.session_changes",
            power=power,
            apps=len(state.get("frozen_apps", [])) + len(state.get("quit_apps", [])),
            services=len(state.get("unloaded_svcs", [])),
        )
    )
    return result

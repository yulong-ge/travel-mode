"""Pure session content, using the same accumulator interval as status."""

from datetime import datetime

from travel_mode.i18n import t
from travel_mode.status import duration
from travel_mode.system.battery import interval


def session_view(state, reading=None, *, now=None):
    active = state.get("session")
    result = dict(title="", lines=[], average_watts=None, remaining_minutes=None, percent_change=None)
    if not active:
        return result
    minutes = max(
        0,
        int(
            (
                (now or datetime.now().astimezone()) - datetime.fromisoformat(active["started_at"])
            ).total_seconds()
            // 60
        ),
    )
    result["title"] = t(
        "redesign.session_title",
        scenario=t("scenario." + (state.get("scenario") or "work")),
        duration=duration(minutes),
    )
    start = active["start"]
    if (
        reading
        and reading.get("available")
        and not active["external_seen"]
        and not reading["external_connected"]
        and not reading["is_charging"]
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
            observed = interval(
                dict(telemetry=start["telemetry"], power_source="PowerTelemetryData.SystemLoad"),
                reading,
                seconds=minutes * 60,
            )
            if observed:
                watts = observed["average_watts"]
                remaining = round(reading["remaining_wh"] / watts * 60)
                result.update(average_watts=watts, remaining_minutes=remaining)
                result["lines"].append(t("redesign.session_power", watts=watts, duration=duration(remaining)))
        if result["average_watts"] is None:
            result["lines"].append(t("redesign.session_incomplete"))
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

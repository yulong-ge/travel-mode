"""Accumulator regression cases, with only public battery fields."""

import json
from pathlib import Path

from travel_mode.system.battery import parse


def interval_case(name):
    directory = Path(__file__).parent / "fixtures/battery"
    fixture = json.loads((directory / "session-interval.json").read_text())

    def reading(values):
        result = parse((directory / "discharging.txt").read_text())
        result.update(
            telemetry={key: values[key] for key in ("system_sum_mw", "system_count")},
            update_time=values["update_time"],
            power_source="PowerTelemetryData.SystemLoad",
        )
        return result

    initial = reading(fixture["start"])
    start = dict(
        at=fixture["started_at"],
        external=False,
        charging=False,
        percent=80,
        remaining_mah=4000,
        voltage_mv=12000,
        telemetry=initial["telemetry"],
        update_time=initial["update_time"],
        errors=[],
    )
    return start, initial, reading(fixture["cases"][name])

"""Fresh hardware generations, not ioreg invocations, determine a measurement."""

import json
from pathlib import Path

import pytest
from jsonschema import validate
from test_battery import FakeClock

from travel_mode import measurement, sessions, state
from travel_mode.system import battery
from travel_mode.system.shell import CmdResult

FIXTURES = Path(__file__).parents[1] / "fixtures/battery"


def row(step, *, watts=20, telemetry=True, count=None, total=None):
    count = 100 + step * 20 if count is None else count
    total = 1_000_000 + step * 20 * watts * 1000 if total is None else total
    text = (
        (FIXTURES / "discharging.txt")
        .read_text()
        .replace('"UpdateTime" = 1000', f'"UpdateTime" = {1000 + step * 20}')
    )
    text = text.rsplit("}", 1)[0]
    if telemetry:
        text += (
            '"PowerTelemetryData" = {'
            f'"AccumulatedSystemLoad"={total},"SystemLoadAccumulatorCount"={count},'
            f'"SystemLoad"={watts * 1000},'
            f'"AccumulatedBatteryDischarge"={2**64 - total},'
            f'"BatteryDischargeAccumulatorCount"={count}' + "}\n"
        )
    return battery.parse(text + "}\n")


def sequence(monkeypatch, rows):
    rows = iter(rows)
    last = None

    def read(**kwargs):
        nonlocal last
        last = next(rows, last)
        return last

    monkeypatch.setattr(battery, "read", read)


def test_repeated_registry_reads_are_insufficient_not_average_watts(monkeypatch):
    sequence(monkeypatch, [row(0)] * 10)
    clock = FakeClock()
    result = measurement.measure(measurement.Settings(seconds=20, interval=5), clock=clock)
    assert result["available"] is False
    assert result["reason"] == "insufficient_samples"
    assert "average_watts" not in result
    assert clock.now == 20


def test_fresh_accumulator_updates_cover_two_intervals_and_ignore_duplicates(monkeypatch):
    sequence(monkeypatch, [row(0), row(0), row(1), row(1), row(2), row(2), row(3)])
    result = measurement.measure(measurement.Settings(seconds=40, interval=5, updates=3), clock=FakeClock())
    assert result["available"] and result["samples"] == 3
    assert result["polls"] == 7 and result["interval_count"] == 2
    assert result["covered_seconds"] == 20 and result["seconds"] == 30
    assert result["power_source"] == "PowerTelemetryData.SystemLoad"
    assert result["average_watts"] == 20
    assert result["remaining_hours"] == pytest.approx(48 / 20)
    assert [r["average_watts"] for r in result["intervals"]] == [20, 20]
    assert result["battery_average_watts"] == 20
    validate(result, json.loads(Path("docs/json-schema/power.schema.json").read_text()))


@pytest.mark.parametrize("count,total", [(1, 1), (1, 2**64 - 1), (160, 1)])
def test_counter_reset_or_wrap_never_becomes_a_power_value(monkeypatch, count, total):
    sequence(monkeypatch, [row(0), row(1), row(2), row(3, count=count, total=total)])
    result = measurement.measure(measurement.Settings(seconds=30, updates=3), clock=FakeClock())
    assert not result["available"] and result["reason"] == "counter_reset"
    assert "average_watts" not in result


def test_voltage_current_only_used_without_telemetry_and_with_fresh_timestamps(monkeypatch):
    sequence(monkeypatch, [row(i, telemetry=False) for i in range(4)])
    result = measurement.measure(measurement.Settings(seconds=30, updates=3), clock=FakeClock())
    assert result["power_source"] == "voltage_current" and result["average_watts"] == 12
    assert result["interval_count"] == 2


def test_source_change_and_external_power_terminate_without_partial_average(monkeypatch):
    for changed, reason in [
        (row(2, telemetry=False), "source_changed"),
        (battery.unavailable("external_power"), "external_power"),
    ]:
        sequence(monkeypatch, [row(0), row(1), changed])
        result = measurement.measure(measurement.Settings(seconds=30, updates=3), clock=FakeClock())
        assert not result["available"] and result["reason"] == reason
        assert "average_watts" not in result


def test_two_deidentified_recordings_have_real_accumulator_delta():
    start, end = [battery.parse((FIXTURES / f"telemetry-{n}.txt").read_text()) for n in (1, 2)]
    result = battery.interval(start, end, seconds=2037)
    assert result["power_source"] == "PowerTelemetryData.SystemLoad"
    assert result["average_watts"] == pytest.approx((1332280892 - 1290870778) / (124749 - 122874) / 1000)
    assert result["battery_average_watts"] == pytest.approx(result["average_watts"])


def test_accumulator_mean_is_weighted_by_counter_delta(monkeypatch):
    sequence(monkeypatch, [row(0), row(1), row(2), row(3, count=180, total=3_000_000)])
    result = measurement.measure(measurement.Settings(seconds=30, updates=3), clock=FakeClock())
    assert [r["average_watts"] for r in result["intervals"]] == [20, 30]
    assert result["average_watts"] == pytest.approx(1600 / 60)


def test_equal_top_timestamp_does_not_hide_advancing_telemetry(monkeypatch):
    sequence(monkeypatch, [{**row(i), "update_time": 1000} for i in range(4)])
    result = measurement.measure(measurement.Settings(seconds=30, updates=3), clock=FakeClock())
    assert result["average_watts"] == 20 and result["samples"] == 3


def test_advancing_top_timestamp_does_not_refresh_stale_telemetry(monkeypatch):
    sequence(monkeypatch, [{**row(0), "update_time": i} for i in range(20)])
    result = measurement.measure(measurement.Settings(seconds=30, updates=3), clock=FakeClock())
    assert result["reason"] == "insufficient_samples" and "average_watts" not in result


def test_counter_wrap_and_fallback_timestamp_reset_are_explicit(monkeypatch):
    for readings in [
        [row(0, count=2**64 - 10), row(1, count=2**64 - 5), row(2, count=1)],
        [row(0, telemetry=False), row(1, telemetry=False), {**row(2, telemetry=False), "update_time": 1}],
    ]:
        sequence(monkeypatch, readings)
        result = measurement.measure(measurement.Settings(seconds=30, updates=3), clock=FakeClock())
        assert result["reason"] == "counter_reset" and "average_watts" not in result


def test_partial_telemetry_is_an_error_not_a_voltage_fallback():
    text = (FIXTURES / "telemetry-2.txt").read_text().replace('"SystemLoadAccumulatorCount"=124749,', "")
    with pytest.raises(battery.BatteryError, match=r"PowerTelemetryData\.SystemLoadAccumulatorCount"):
        battery.parse(text)


def test_missing_freshness_marker_cannot_produce_measurement(monkeypatch):
    sequence(monkeypatch, [{**row(0, telemetry=False), "update_time": None}])
    result = measurement.measure(clock=FakeClock())
    assert result["reason"] == "missing_update_marker" and "average_watts" not in result


def test_whole_session_uses_two_recorded_accumulators_without_a_sampler(fake_runner, monkeypatch):
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult("2020-01-01 00:00:00 Using Batt\n", "", 0)
    fake_runner.responses[tuple(battery.COMMAND)] = CmdResult(
        (FIXTURES / "telemetry-1.txt").read_text(), "", 0
    )
    monkeypatch.setattr(sessions, "utc_now", lambda: "2026-10-15T10:00:00+00:00")
    st = state.new_state()
    st["scenario"] = "work"
    sessions.begin(st)
    fake_runner.responses[tuple(battery.COMMAND)] = CmdResult(
        (FIXTURES / "telemetry-2.txt").read_text(), "", 0
    )
    monkeypatch.setattr(sessions, "utc_now", lambda: "2026-10-15T10:33:57+00:00")
    report = sessions.build_report(st, 1, 0)
    assert report["average_scope"] == "session"
    assert report["average_source"] == "PowerTelemetryData.SystemLoad"
    assert report["average_watts"] == pytest.approx(41410114 / 1875 / 1000)
    from travel_mode.i18n import t

    assert t("session.scope.telemetry") in "\n".join(sessions.describe(report))
    assert fake_runner.calls.count(tuple(battery.COMMAND)) == 2
    sessions.validate_report(report)
    validate(
        {"version": 1, "limit": 50, "sessions": [{**report, "comparison": None}]},
        json.loads(Path("docs/json-schema/history.schema.json").read_text()),
    )


def test_whole_session_never_averages_reset_or_charging_telemetry(fake_runner, monkeypatch):
    start = {
        "at": "2026-10-15T10:00:00+00:00",
        "external": False,
        "charging": False,
        "percent": 80,
        "remaining_mah": 4000,
        "voltage_mv": 12000,
        "errors": [],
        "telemetry": row(2)["telemetry"],
    }
    end = {
        **start,
        "at": "2026-10-15T11:00:00+00:00",
        "remaining_mah": 3900,
        "telemetry": row(0)["telemetry"],
    }
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult("2020-01-01 00:00:00 Using Batt\n", "", 0)
    monkeypatch.setattr(sessions, "observe", lambda: start)
    st = state.new_state()
    sessions.begin(st)
    monkeypatch.setattr(sessions, "observe", lambda: end)
    report = sessions.build_report(st, 1, 0)
    assert report["average_watts"] is None and report["errors"]
    end.update(external=True, telemetry=row(3)["telemetry"])
    report = sessions.build_report(st, 1, 0)
    assert report["power_status"] == "external_seen" and report["average_watts"] is None


@pytest.mark.parametrize("language", ["en", "zh"])
def test_default_two_updates_yield_one_lower_precision_interval(monkeypatch, language):
    from travel_mode.i18n import set_language, t

    set_language(language)
    settings = measurement.Settings()
    assert settings.seconds == 180 and settings.updates == 2
    measurement.validate_settings(settings)
    sequence(monkeypatch, [row(0), row(0), row(1), row(1), row(2)])
    result = measurement.measure(clock=FakeClock())
    assert result["available"] and result["samples"] == 2
    assert result["interval_count"] == 1 and result["polls"] == 5
    assert result["covered_seconds"] == 10 and result["seconds"] == 20
    assert result["average_watts"] == 20
    assert t("measurement.single_interval") in measurement.describe(result)
    validate(result, json.loads(Path("docs/json-schema/power.schema.json").read_text()))
    comparison = {"before": result, "after": result, "saved_watts": 0, "extra_hours": 0}
    measurement.validate_comparison(comparison)
    assert t("measurement.single_interval") in measurement.comparison_description(comparison)
    assert "180" in t("measurement.wait", seconds=settings.seconds, updates=settings.updates)
    with pytest.raises(ValueError, match="updates"):
        measurement.validate_settings(measurement.Settings(updates=1))

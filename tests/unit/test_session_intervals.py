"""Old or incomplete observations are unavailable estimates, not UI faults."""

from datetime import datetime, timedelta

import pytest

from battery_support import FakeClock
from session_interval_support import interval_case
from travel_mode import measurement, sessions, state, status
from travel_mode.i18n import set_language, t
from travel_mode.system import battery
from travel_mode.system.shell import CmdResult
from travel_mode.tui.session_view import session_view


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("case", ["stale", "unchanged", "reset", "normal"])
def test_session_interval_classification(case, language, monkeypatch):
    set_language(language)
    start, initial, reading = interval_case(case)
    monkeypatch.setattr(sessions, "observe", lambda: start)
    st = state.new_state()
    sessions.begin(st)
    now = datetime.fromisoformat(start["at"]) + timedelta(seconds=20)
    view = session_view(st, reading, now=now)
    if case == "normal":
        # Fresh counters in the first minute must not be mistaken for a reset.
        expected = (3118964536 - 3104325514) / (486471 - 458963) / 1000
        assert view["average_watts"] == pytest.approx(expected)
        assert view["remaining_minutes"] is not None
    else:
        assert view["average_watts"] is None and view["remaining_minutes"] is None
        key = "battery.interval_discontinuous" if case == "reset" else "battery.interval_waiting"
        assert t(key) in view["lines"]
    if case == "stale":
        assert view["percent_change"] is None
    assert initial["telemetry"]["system_count"] == 458963


@pytest.mark.parametrize("offset", [-1, 0, 0.5])
def test_cached_or_inflight_read_cannot_end_a_new_session_without_hardware_timestamp(monkeypatch, offset):
    start, _, reading = interval_case("normal")
    start.pop("update_time")
    reading.pop("update_time")
    monkeypatch.setattr(sessions, "observe", lambda: start)
    st = state.new_state()
    sessions.begin(st)
    started_at = datetime.fromisoformat(start["at"])
    view = session_view(
        st,
        reading,
        now=started_at + timedelta(seconds=30),
        reading_at=started_at + timedelta(seconds=offset),
    )
    assert view["average_watts"] is None and view["percent_change"] is None
    assert t("battery.interval_waiting") in view["lines"]


def test_equal_counters_and_zero_duration_never_imply_a_reset():
    _, initial, reading = interval_case("unchanged")
    assert battery.interval(initial, reading, seconds=30) is None
    _, _, fresh = interval_case("normal")
    assert battery.interval(initial, fresh, seconds=0) is None
    with pytest.raises(ValueError, match=t("battery.interval_order_invalid")):
        battery.interval(initial, fresh, seconds=-1)
    assert (
        battery.interval(
            initial, {**fresh, "telemetry": {**initial["telemetry"], "system_count": 486471}}, seconds=30
        )
        is None
    )


@pytest.mark.parametrize("case", ["stale", "unchanged", "reset", "normal"])
@pytest.mark.parametrize("phase", ["before", "after"])
def test_measurement_windows_keep_fresh_baseline(case, phase, monkeypatch):
    _, initial, reading = interval_case(case)
    fresh = {
        **initial,
        "telemetry": {"system_count": 458983, "system_sum_mw": 3104345514},
        "update_time": initial["update_time"] + 20,
    }
    later = {
        **fresh,
        "telemetry": {"system_count": 459003, "system_sum_mw": 3104385514},
        "update_time": fresh["update_time"] + 20,
    }
    # The first successful window is 2 W. A stale/partial observation must not
    # replace its baseline or count towards either window's two fresh updates.
    good = [initial, fresh, later]
    tested = [initial, reading]
    if case in {"stale", "unchanged"}:
        tested += [fresh, later]
    elif case == "normal":
        tested += [
            {
                **reading,
                "telemetry": {
                    "system_count": reading["telemetry"]["system_count"] + 20,
                    "system_sum_mw": reading["telemetry"]["system_sum_mw"] + 40000,
                },
                "update_time": reading["update_time"] + 20,
            }
        ]
    rows = iter(tested + good if phase == "before" else good + tested)
    monkeypatch.setattr(battery, "read", lambda **kwargs: next(rows))
    settings = measurement.Settings(seconds=30, settle_seconds=0)
    clock = FakeClock()
    before = measurement.begin("away", True, settings, lambda text: None, clock=clock)
    st = {}
    result = measurement.finish(before, settings, st, lambda value: None, lambda text: None, clock=clock)
    window = result[phase]
    if case == "reset":
        assert not window["available"] and window["reason"] == "counter_reset"
        assert result["saved_watts"] is None
    else:
        assert window["available"] and window["average_watts"] == 2
        assert window["samples"] == 2 and window["interval_count"] == 1
        assert window["polls"] == (4 if case in {"stale", "unchanged"} else 3)
        assert window["intervals"][0]["start_generation"] != initial["telemetry"]["system_count"]
    measurement.validate_comparison(result)


@pytest.mark.parametrize("case", ["stale", "unchanged", "reset", "normal"])
def test_report_and_menu_preserve_snapshot_order(case, monkeypatch, fake_runner):
    start, _, reading = interval_case(case)
    monkeypatch.setattr(sessions, "observe", lambda: start)
    st = state.new_state()
    st["scenario"] = "away"
    sessions.begin(st)
    state.save_state(st)
    end = {
        **start,
        "at": "2026-10-20T04:21:05+00:00",
        "telemetry": reading["telemetry"],
        "update_time": reading["update_time"],
    }
    monkeypatch.setattr(sessions, "observe", lambda: end)
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult("", "", 0)
    monkeypatch.setattr(battery, "read", lambda **kwargs: reading)
    report = sessions.build_report(st, 0, 0)
    menu = status.data(now=datetime.fromisoformat(end["at"]))["battery"]
    if case == "normal":
        assert report["average_watts"] == menu["average_watts"]
        assert report["average_scope"] == "session"
    else:
        key = "battery.interval_discontinuous" if case == "reset" else "battery.interval_waiting"
        assert report["average_watts"] is None and menu["average_watts"] is None
        assert t(key) in report["errors"] and menu["estimate_error"] == t(key)
        if case == "stale":
            assert report["percent_drop"] is None and report["mah_drop"] is None
    sessions.validate_report(report)

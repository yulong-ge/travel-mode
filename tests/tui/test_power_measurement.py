"""Open-tool timers and button sampling stay bounded and read-only."""

import json
from pathlib import Path

import pytest
from textual.widgets import Checkbox, Static

from battery_support import FakeClock, fresh_readings
from travel_mode import measurement
from travel_mode.system import battery
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout
from tui_plan import select_plan


async def test_battery_timer_is_30_seconds_and_energy_is_button_only(tmp_paths, fake_runner, monkeypatch):
    app = TravelApp(scenario="away")
    original = app.set_interval
    observed = {}
    original_parse, parsed = battery.parse, []

    def parse(text, **kwargs):
        parsed.append(text)
        return original_parse(text, **kwargs)

    monkeypatch.setattr(battery, "parse", parse)

    class Timer:
        stopped = False

        def stop(self):
            self.stopped = True

    def set_interval(seconds, callback, **kwargs):
        if callback.__name__ == "refresh_battery":
            observed.update(seconds=seconds, callback=callback, timer=Timer())
            return observed["timer"]
        return original(seconds, callback, **kwargs)

    monkeypatch.setattr(app, "set_interval", set_interval)
    async with app.run_test(size=(120, 45)):
        await app.wait_for_idle()
        assert app.refresh_complete
        assert observed["seconds"] == 30
        assert not any(call[0] == "top" for call in fake_runner.calls)
        fake_runner.calls.clear()
        parsed.clear()
        observed["callback"]()
        await app.wait_for_idle()
        assert fake_runner.calls == [tuple(battery.COMMAND)]
        assert len(parsed) == 1
        await app.utility_action("b-energy")
        await app.wait_for_idle()
        assert sum(call[0] == "top" for call in fake_runner.calls) == 1
    assert observed["timer"].stopped
    assert app._measurement_cancelled.is_set()


async def test_tui_work_comparison_uses_fake_clock_and_persists(tmp_paths, fake_runner, monkeypatch):
    (tmp_paths / "config.toml").write_text("[measurement]\nseconds=20\ninterval=5\nsettle_seconds=10\n")
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    reading = battery.parse((Path(__file__).parents[1] / "fixtures/battery/discharging.txt").read_text())
    fresh_readings(
        monkeypatch,
        lambda: {
            **reading,
            "watts": 6 if any(call[:3] == ("sudo", "pmset", "-b") for call in fake_runner.calls) else 12,
        },
    )

    class Clock:
        now = 0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(measurement, "CancellableClock", lambda event: clock)
    app = TravelApp(scenario="work")
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        assert app.refresh_complete
        await select_plan(app, ids={r["id"] for r in app.plan_items if r["descriptor"]["kind"] == "power"})
        app.query_one("#q-measure", Checkbox).value = True
        assert app.query_one("#q-measure", Checkbox).value
        await click_after_layout(pilot, "#b-quick")
        await app.wait_for_idle()
        assert app.st["measurement"]["saved_watts"] == 6
        assert clock.now == 30
        assert app.st["measurement"]["before"]["interval_count"] == 1
        assert app.st["measurement"]["after"]["interval_count"] == 1
        assert str(app.query_one("#measurement-progress", Static).content)
        assert json.loads((tmp_paths / "state.json").read_text())["measurement"] == app.st["measurement"]


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_insufficient_fresh_updates_do_not_stop_tui_actions_or_fabricate_savings(
    language, tmp_paths, fake_runner, monkeypatch
):
    from travel_mode import sessions, state
    from travel_mode.i18n import set_language

    set_language(language)
    (tmp_paths / "config.toml").write_text("[measurement]\nseconds=20\ninterval=5\nsettle_seconds=0\n")
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    reading = battery.parse((Path(__file__).parents[1] / "fixtures/battery/discharging.txt").read_text())
    monkeypatch.setattr(battery, "read", lambda **kwargs: reading)
    clock = FakeClock()
    monkeypatch.setattr(measurement, "CancellableClock", lambda event: clock)
    app = TravelApp(scenario="work")
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        await select_plan(app, ids={r["id"] for r in app.plan_items if r["descriptor"]["kind"] == "power"})
        app.query_one("#q-measure", Checkbox).value = True
        await click_after_layout(pilot, "#b-quick")
        await app.wait_for_idle()
        saved = state.load_state()
        window = saved["measurement"]["before"]
        assert window["reason"] == "insufficient_samples" and window["samples"] == 0
        assert "average_watts" not in window
        assert saved["measurement"]["after"] is None
        assert saved["measurement"]["saved_watts"] is None
        assert clock.now == 20 and any(c[:3] == ("sudo", "pmset", "-b") for c in fake_runner.calls)
        sessions.validate_report(sessions.build_report(saved, 1, 0))
        assert not app._mutation_lock.locked()


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("phase", ["before", "after"])
async def test_measurement_read_failure_keeps_tui_action_and_report_available(
    phase, language, tmp_paths, fake_runner, monkeypatch
):
    from travel_mode import sessions, state
    from travel_mode.i18n import set_language, t
    from travel_mode.system.shell import ReadError

    set_language(language)
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    calls = []

    def measure(*args, **kwargs):
        calls.append(len(calls))
        if len(calls) == (1 if phase == "before" else 2):
            raise ReadError("fixture ioreg timeout")
        return dict(
            available=True,
            reason=None,
            samples=1,
            seconds=2,
            average_watts=12,
            remaining_wh=48,
            remaining_hours=4,
            macos=dict(raw="fixture", minutes=None),
        )

    class Clock:
        def sleep(self, seconds):
            assert seconds == 0

    (tmp_paths / "config.toml").write_text("[measurement]\nsettle_seconds=0\n")
    monkeypatch.setattr(measurement, "measure", measure)
    monkeypatch.setattr(measurement, "CancellableClock", lambda event: Clock())
    app = TravelApp(scenario="work")
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        await select_plan(app, ids={r["id"] for r in app.plan_items if r["descriptor"]["kind"] == "power"})
        app.query_one("#q-measure", Checkbox).value = True
        await click_after_layout(pilot, "#b-quick")
        await app.wait_for_idle()
        assert any(c[:3] == ("sudo", "pmset", "-b") for c in fake_runner.calls)
        saved = state.load_state()
        result = saved["measurement"]
        failed = result[phase]
        assert failed["reason"] == "failed" and "timeout" in failed["error"]
        report = sessions.build_report(saved, 1, 0)
        assert t("report.measure_failed") in "\n".join(sessions.describe(report))
        assert t("measurement.failed", reason=failed["error"]) in "\n".join(
            sessions.describe(report, verbose=True)
        )
        assert not app._mutation_lock.locked()


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_overview_battery_read_failure_does_not_stop_tui(language, monkeypatch):
    from travel_mode.i18n import set_language, t
    from travel_mode.system.shell import ReadError

    set_language(language)
    app = TravelApp(scenario="away")
    async with app.run_test(size=(120, 45)):
        await app.wait_for_idle()

        def fail():
            raise ReadError("fixture ioreg timeout")

        monkeypatch.setattr(battery, "read", fail)
        app.refresh_battery()
        await app.wait_for_idle()
        assert app.is_running and app.refresh_complete
        assert t("measurement.failed", reason="fixture ioreg timeout") in str(
            app.query_one("#dash-battery", Static).content
        )

"""Open-tool timers and button sampling stay bounded and read-only."""

import json
from pathlib import Path

import pytest
from textual.widgets import Checkbox, Static

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


@pytest.mark.parametrize("before_watts,after_watts", [(8.8, 8.5), (23.1, 13.0)])
async def test_tui_passive_comparison_never_waits_and_fills_on_refresh(
    tmp_paths, fake_runner, monkeypatch, before_watts, after_watts
):
    from datetime import datetime, timedelta

    from travel_mode import state

    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    reading = battery.parse((Path(__file__).parents[1] / "fixtures/battery/discharging.txt").read_text())
    monkeypatch.setattr(battery, "read", lambda **kwargs: reading)
    monkeypatch.setattr(measurement, "begin", lambda *a, **kw: pytest.fail("blocking before measurement"))
    monkeypatch.setattr(measurement, "finish", lambda *a, **kw: pytest.fail("blocking after measurement"))
    app = TravelApp(scenario="away", measure=True)
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        base = datetime.now().astimezone() - timedelta(seconds=60)
        app.passive_measurement.previous = (dict(reading, update_time=100, watts=before_watts), base)
        app.passive_measurement.observe(
            dict(reading, update_time=101, watts=before_watts), base + timedelta(seconds=30)
        )
        await select_plan(app, ids={r["id"] for r in app.plan_items if r["descriptor"]["kind"] == "power"})
        app.query_one("#q-measure", Checkbox).value = True
        await click_after_layout(pilot, "#b-quick")
        await app.wait_for_idle()
        assert "measurement" in app.st, (app.last_result, app.st, app.last_batch)
        assert app.st["measurement"]["after"] is None
        assert any(c[:3] == ("sudo", "pmset", "-b") for c in fake_runner.calls)
        assert "正在等待新的电池数据" in "\n".join(app.session_view["lines"])
        # Feed real refresh entry points; no new timer and no waiting clock.
        # The post-action discovery refresh can already have observed a cached
        # generation. Start the controlled refresh sequence at this boundary.
        app.passive_measurement.complete(datetime.now().astimezone())
        reading = dict(reading, update_time=102, watts=after_watts)
        app.refresh_battery()
        await app.wait_for_idle()
        assert app.st["measurement"]["after"] is None
        reading = dict(reading, update_time=103)
        app.refresh_battery()
        await app.wait_for_idle()
        assert app.st["measurement"]["after"] is None
        reading = dict(reading, update_time=104)
        app.refresh_battery()
        await app.wait_for_idle()
        comparison = app.st["measurement"]
        assert comparison["after"]["average_watts"] == after_watts
        assert comparison["saved_watts"] == pytest.approx(before_watts - after_watts)
        assert state.load_state()["measurement"] == comparison
        assert json.loads((tmp_paths / "state.json").read_text())["measurement"] == comparison
        assert not app._mutation_lock.locked()


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_no_pre_interval_executes_immediately_without_fabricating_savings(
    language, tmp_paths, fake_runner, monkeypatch
):
    from travel_mode import sessions, state
    from travel_mode.i18n import set_language

    set_language(language)
    (tmp_paths / "config.toml").write_text("[measurement]\nautomatic=true\n")
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    monkeypatch.setattr(measurement, "begin", lambda *a, **kw: pytest.fail("legacy config must not block"))
    app = TravelApp(scenario="work")
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        await select_plan(app, ids={r["id"] for r in app.plan_items if r["descriptor"]["kind"] == "power"})
        app.query_one("#q-measure", Checkbox).value = True
        await click_after_layout(pilot, "#b-quick")
        await app.wait_for_idle()
        saved = state.load_state()
        assert saved["measurement"]["before"]["reason"] == "insufficient_samples"
        assert saved["measurement"]["after"] is None
        assert saved["measurement"]["saved_watts"] is None
        assert any(c[:3] == ("sudo", "pmset", "-b") for c in fake_runner.calls)
        sessions.validate_report(sessions.build_report(saved, 1, 0))
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

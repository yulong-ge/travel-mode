"""Open-tool timers and button sampling stay bounded and read-only."""

import json
from pathlib import Path

from textual.widgets import Checkbox, Static

from travel_mode import measurement
from travel_mode.system import battery
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout


async def test_battery_timer_is_30_seconds_and_energy_is_button_only(tmp_paths, fake_runner, monkeypatch):
    app = TravelApp(scenario="away")
    original = app.set_interval
    observed = {}

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
    async with app.run_test(size=(120, 45)) as pilot:
        await app.workers.wait_for_complete()
        await app.workers.wait_for_complete()
        assert app.refresh_complete
        assert observed["seconds"] == 30
        assert not any(call[0] == "top" for call in fake_runner.calls)
        fake_runner.calls.clear()
        observed["callback"]()
        await app.workers.wait_for_complete()
        assert fake_runner.calls == [tuple(battery.COMMAND)]
        await click_after_layout(pilot, "#b-energy")
        await app.workers.wait_for_complete()
        assert sum(call[0] == "top" for call in fake_runner.calls) == 1
    assert observed["timer"].stopped
    assert app._measurement_cancelled.is_set()


async def test_tui_work_comparison_uses_fake_clock_and_persists(tmp_paths, fake_runner, monkeypatch):
    (tmp_paths / "config.toml").write_text("[measurement]\nseconds=2\ninterval=2\nsettle_seconds=10\n")
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    reading = battery.parse((Path(__file__).parents[1] / "fixtures/battery/discharging.txt").read_text())
    monkeypatch.setattr(
        battery,
        "read",
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
        await app.workers.wait_for_complete()
        await app.workers.wait_for_complete()
        assert app.refresh_complete
        for checkbox in app.query("#p-dash Checkbox"):
            checkbox.value = checkbox.id in {"q-power", "q-measure"}
        assert app.query_one("#q-measure", Checkbox).value
        await click_after_layout(pilot, "#b-quick")
        await app.workers.wait_for_complete()
        assert app.st["measurement"]["saved_watts"] == 6
        assert clock.now == 14
        assert str(app.query_one("#measurement-progress", Static).content)
        assert json.loads((tmp_paths / "state.json").read_text())["measurement"] == app.st["measurement"]

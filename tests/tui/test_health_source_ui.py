"""One source line owns its explanation and existing exact-PID Action."""

import pytest
from textual.widgets import Button

from health_source_fixture import populate
from travel_mode.i18n import set_language, t
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_caffeinate_source_has_one_line_and_stop_action(fake_runner, monkeypatch, language):
    populate(fake_runner, load=True, awake_app=True)
    set_language(language)
    stopped = []
    monkeypatch.setattr(
        "travel_mode.system.processes.terminate_caffeinate",
        lambda target: (stopped.append(target.process.pid) or "", 0),
    )
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        readiness = "\n".join(str(w.content) for w in app.query("#sleep-ready Static"))
        attention = "\n".join(str(w.content) for w in app.query("#dash-health Static"))
        assert readiness.count("Claude Code") == 1 and "caffeinate" in readiness
        assert "caffeinate" not in attention and "Claude Code" not in attention
        assert "Amphetamine" in attention
        assert "worker-0" in attention and "85%" in attention
        button = app.query_one("#ready-0", Button)
        assert str(button.label) == t("health.action.caffeinate")
        await click_after_layout(pilot, "#ready-0")
        await app.wait_for_idle()
        assert stopped == [900100] and app.is_running

"""Reports and local history use committed frames and no host observations."""

import pytest
from textual.widgets import Static

from travel_mode import sessions, state
from travel_mode.i18n import set_language, t
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_restore_report_and_history_share_scrollable_view(
    language, fake_runner, monkeypatch, tmp_paths
):
    set_language(language)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        # A persisted session with no outstanding system mutations is enough to
        # exercise report archival, deletion ordering, display and local history.
        sessions.begin(app.st)
        app.save()
        app.action_goto("p-net")
        await click_after_layout(pilot, "#b-off")
        await app.wait_for_idle()
        text = str(app.screen.query_one("#session-text", Static).content)
        assert t("session.heading", scenario="work") in text
        assert not state._path().exists() and len(sessions.read_history()) == 1
        await pilot.press("escape")
        await app.screen.wait_for_refresh()
        calls = list(fake_runner.calls)
        await click_after_layout(pilot, "#b-history")
        assert t("session.heading", scenario="work") in str(
            app.screen.query_one("#session-text", Static).content
        )
        assert fake_runner.calls == calls
        await pilot.press("escape")


async def test_overview_has_one_status_health_region_and_one_sleep_fact(fake_runner):
    from travel_mode.system.shell import CmdResult

    fake_runner.responses[("pmset", "-g")] = CmdResult("SleepDisabled 1\nlowpowermode 0\n", "", 0)
    app = TravelApp(scenario="away")
    async with app.run_test(size=(80, 24)):
        await app.wait_for_idle()
        assert len(app.query("#overview-health.card")) == 1
        assert not app.query("#dash-status.card") and not app.query("#dash-health.card")
        text = "\n".join(str(w.content) for w in app.query("#overview-health Static"))
        assert text.count("SleepDisabled=1") == 1


@pytest.mark.parametrize("scenario", ["work", "away"])
async def test_e2e_scenario_selector_is_mounted_and_enters_requested_scenario(scenario):
    import asyncio

    app = TravelApp(scenario="ask", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        assert await asyncio.wait_for(app.screen.wait_for_refresh(), 5)
        button = app.query_one("#scenario-" + scenario)
        assert button.is_attached and app.screen.region.contains_region(button.region)
        assert await pilot.click(button, offset=(button.region.width // 2, 1))
        await app.wait_for_idle()
        assert app.scenario == scenario and app.refresh_complete

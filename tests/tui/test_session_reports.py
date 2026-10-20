"""Reports and local history use committed frames and no host observations."""

import asyncio
import json
import time
from pathlib import Path

import pytest
from textual.widgets import Static

from travel_mode import sessions, state
from travel_mode.i18n import set_language, t
from travel_mode.tui.app import TravelApp
from travel_mode.tui.session_screen import SessionScreen
from tui_interaction import click_after_layout, modal_result


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 45)])
async def test_readable_history_dialog_shares_report_and_local_time(language, size, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    set_language(language)
    report = json.loads((Path(__file__).parents[1] / "fixtures/session-wording.json").read_text())
    sessions.append_history(report, 50)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=size):
        await app.wait_for_idle()
        await app.show_history()
        assert await app.screen.wait_for_refresh()
        text = str(app.screen.query_one("#session-text", Static).content)
        assert text == "\n".join(sessions.describe(report))
        assert "16:26" in text and "09:01" in text
        assert "mAh" not in text and "timeout" not in text and "osascript" not in text
        assert app.screen.region.contains_region(app.screen.query_one("#session-close").region)
        app.screen.dismiss()


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
        app.view_mode = "session"
        app.update_primary()
        with modal_result(monkeypatch, app, SessionScreen) as closed:
            await click_after_layout(pilot, "#b-off")
            await app.wait_for_idle()
            text = str(app.screen.query_one("#session-text", Static).content)
            assert t("session.heading", scenario=t("scenario.work")) in text
            assert not state._path().exists() and len(sessions.read_history()) == 1
            await pilot.press("escape")
            await asyncio.wait_for(closed, 10)
        assert await app.screen.wait_for_refresh()
        calls = list(fake_runner.calls)
        with modal_result(monkeypatch, app, SessionScreen) as closed:
            await app.utility_action("b-history")
            assert t("session.heading", scenario=t("scenario.work")) in str(
                app.screen.query_one("#session-text", Static).content
            )
            assert fake_runner.calls == calls
            await pilot.press("escape")
            await asyncio.wait_for(closed, 10)
        assert await app.screen.wait_for_refresh()


async def test_overview_has_one_status_health_region_and_one_sleep_fact(fake_runner):
    from travel_mode.system.shell import CmdResult

    fake_runner.responses[("pmset", "-g")] = CmdResult("SleepDisabled 1\nlowpowermode 0\n", "", 0)
    app = TravelApp(scenario="away")
    async with app.run_test(size=(80, 24)):
        await app.wait_for_idle()
        assert len(app.query("#sleep-ready")) == 1
        assert not app.query("#dash-status.card") and not app.query("#dash-health.card")
        text = "\n".join(str(w.content) for w in app.query("#sleep-ready Static"))
        assert text.count(t("redesign.sleep_disabled")) == 1
        assert "SleepDisabled=1" not in text


@pytest.mark.parametrize("scenario", ["work", "away"])
async def test_e2e_scenario_selector_is_mounted_and_enters_requested_scenario(scenario):
    app = TravelApp(scenario="ask", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        assert await asyncio.wait_for(app.screen.wait_for_refresh(), 5)
        button = app.query_one("#scenario-" + scenario)
        assert button.is_attached and app.screen.region.contains_region(button.region)
        assert await pilot.click(button, offset=(button.region.width // 2, 1))
        await pilot.press("enter")
        await app.wait_for_idle()
        assert app.scenario == scenario and app.refresh_complete

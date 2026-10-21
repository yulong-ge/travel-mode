"""A completed row is visible while another app's quit is still pending."""

import asyncio
from threading import Event

import pytest

from travel_mode.failures import log_path
from travel_mode.i18n import set_language, t
from travel_mode.system import shell
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout
from tui_plan import app_item, select_plan


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_partial_completion_is_visible_before_the_quit_batch_finishes(machine, monkeypatch, language):
    set_language(language)
    app = TravelApp(scenario="work", measure=False)
    release = Event()
    release_freeze = Event()
    started = asyncio.Event()
    completed = asyncio.Event()
    original_runner = shell.get_runner()
    refresh_row = app.refresh_row

    def runner(command, timeout=None, interactive=False):
        if command[0] == "osascript":
            assert release.wait(30), "test did not release the pending fixture quit"
        if command[:2] == ["kill", "-STOP"]:
            assert release_freeze.wait(30), "test did not release the pending fixture freeze"
        return original_runner(command, timeout, interactive)

    async def refreshed(row):
        await refresh_row(row)
        if row["name"] == "Freeze Demo" and row["state"] == "running":
            started.set()
        if row["name"] == "Freeze Demo" and row["state"] == "done":
            completed.set()

    monkeypatch.setattr(app, "refresh_row", refreshed)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        await select_plan(app, names={"Quit Demo", "Freeze Demo"})
        shell.set_runner(runner)
        button = app.main_query_one("#b-quick")
        button.dispatch_posted.clear()
        button.press()
        try:
            await asyncio.wait_for(button.dispatch_posted.wait(), 30)
            await asyncio.wait_for(started.wait(), 30)
            assert str(app.main_query_one("#result-summary").content) == t(
                "redesign.result_running", total=2, completed=0
            )
            assert str(app.main_query_one("#last-result").content) == ""
            release_freeze.set()
            await asyncio.wait_for(completed.wait(), 30)
            assert app.view_mode == "result"
            assert app_item(app, "Quit Demo")["state"] == "running"
            assert app_item(app, "Freeze Demo")["state"] == "done"
            assert app.last_batch == dict(ok=1, failed=0)
            assert str(app.main_query_one("#result-summary").content) == t(
                "redesign.result_running", total=2, completed=1
            )
            assert app.main_query_one("#b-complete").disabled
        finally:
            release_freeze.set()
            release.set()
        await asyncio.wait_for(asyncio.shield(button.last_dispatch), 30)
        await app.wait_for_idle()
        assert app_item(app, "Quit Demo")["state"] == "done"
        assert not app.main_query_one("#b-complete").disabled
        assert (
            str(app.main_query_one("#result-summary").content)
            in app.main_query_one("#last-result").full_text().plain
        )
        assert str(app.main_query_one("#result-summary").content) == t(
            "redesign.result_summary", ok=2, failed=0
        )
        # Final details are useful only when the batch has a warning or failure.
        for status, counts in [("warning", dict(ok=2, failed=0)), ("failed", dict(ok=1, failed=1))]:
            app_item(app, "Quit Demo")["state"] = status
            await app.render_plan()
            assert str(app.main_query_one("#result-summary").content) == t(
                "redesign.result_summary", **counts
            ) + t("redesign.result_details", path=log_path())


async def test_grouped_power_and_refusal_use_same_row_counts_in_summary_and_footer(machine, monkeypatch):
    from travel_mode.system.shell import CmdResult

    app = TravelApp(scenario="work", measure=False)
    original = shell.get_runner()

    def refused(command, timeout=None, interactive=False):
        if command[0] == "osascript":
            return CmdResult("", "User canceled (-128)", 1)
        return original(command, timeout, interactive)

    class Clock:
        now = 0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr("travel_mode.system.apps.QuitClock", lambda: clock)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        ids = {r["id"] for r in app.plan_items if r["descriptor"].get("domain") == "-c"}
        assert len(ids) == 2
        ids.add(app_item(app, "Quit Demo")["id"])
        await select_plan(app, ids=ids)
        shell.set_runner(refused)
        await click_after_layout(pilot, "#b-quick")
        await app.wait_for_idle()
        assert app.last_batch == dict(ok=2, failed=1)
        counts = dict(attempted=3, succeeded=2, failed=1)
        assert app.st["session"]["actions"] == counts
        summary = str(app.main_query_one("#result-summary").content)
        assert summary in app.main_query_one("#last-result").full_text().plain
        assert "-128" in str(app.st["action_failures"])
        from travel_mode import sessions

        report = sessions.build_report(app.st, 0, 0)
        assert report["actions"] == counts
        assert t("report.actions", **counts) in "\n".join(sessions.describe(report))
        assert report["resurrected"] == []
        from travel_mode import resurrection

        assert not any(row["path"] == machine["paths"]["Quit Demo"] for row in resurrection.detect(app.st))

"""User decisions and terminal rows use committed state, not timed sleeps."""

import asyncio

import pytest
from textual.widgets import RichLog

from travel_mode import cli, state
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from travel_mode.tui.conflict import SessionConflict
from tui_interaction import click_after_layout
from tui_plan import app_item, apply_plan


@pytest.mark.parametrize("choice", ["cancel", "continue", "restore"])
async def test_existing_session_decision_precedes_tui_mutations(machine, choice):
    from travel_mode.failures import mutation

    assert cli.main(["on", "--scenario", "away", "--yes", "--no-measure"]) == 0
    old = state.load_state()
    machine["fake"].calls.clear()
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(160, 65)) as pilot:
        await app.wait_for_idle()
        assert app.st["scenario"] == "away"
        app.view_mode = "plan"
        app.update_primary()
        await click_after_layout(pilot, "#b-quick")
        assert isinstance(app.screen, SessionConflict)
        assert not any(mutation(c) for c in machine["fake"].calls)
        button = app.screen.query_one("#conflict-" + choice)
        button.focus()
        await pilot.press("enter")
        await asyncio.wait_for(app.session_decision_complete.wait(), 30)
        await app.wait_for_idle()
        assert app.is_running and app._exception is None
        result = state.load_state()
        if choice == "cancel":
            assert result == old
        elif choice == "continue":
            assert result["scenario"] == "away"
            assert result["session"]["id"] == old["session"]["id"]
            assert result["session"]["appended_scenarios"] == ["work"]
        else:
            assert result["scenario"] == "work"
            assert (machine["root"] / "history.jsonl").exists()


async def test_refused_row_terminal_success_hides_retry_and_force_after_refresh(machine):
    machine["fake"].responses[("osascript",)] = CmdResult("", "User canceled. (-128)", 1)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(160, 65)) as pilot:
        await app.wait_for_idle()
        await apply_plan(pilot, names={"Quit Demo"}, action="quit")
        row = app_item(app, "Quit Demo")
        assert row["state"] == "failed" and row["operations"] == ["retry", "force"]
        await click_after_layout(pilot, "#force-" + row["id"])
        assert row["state"] == "done" and not row["operations"]
        assert not app.query("#force-" + row["id"])
        assert not app.query("#retry-" + row["id"])
        app.action_refresh()
        await app.wait_for_idle()
        assert not any(r["name"] == "Quit Demo" and r["operations"] for r in app.plan_items)
        assert "Traceback" not in "\n".join(str(line) for line in app.query_one("#log", RichLog).lines)

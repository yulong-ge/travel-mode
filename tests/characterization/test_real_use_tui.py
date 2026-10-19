"""User decisions and terminal rows use committed state, not timed sleeps."""

import asyncio

import pytest
from textual.widgets import RichLog

from travel_mode import cli, state
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from travel_mode.tui.conflict import SessionConflict
from tui_interaction import click_after_layout


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
        app.action_goto("p-apps")
        index = next(i for i, r in app.app_rows.items() if r["name"] == "Quit Demo")
        await click_after_layout(pilot, f"#btn-{index}-q")
        assert app.app_rows[index]["view"]["state"] == "refused"
        assert app.query_one(f"#btn-{index}-r").display
        await click_after_layout(pilot, f"#btn-{index}-q")
        row = app.app_rows[index]
        assert row["view"]["state"] == "exited" and not row["kill"]
        assert not row["view"]["operations"]
        assert all(not b.display and b.disabled for b in row["btns"])
        app.action_refresh()
        await app.wait_for_idle()
        assert not any(v["name"] == "Quit Demo" and "force_quit" in v["operations"] for v in app.app_views)
        assert "Traceback" not in "\n".join(str(line) for line in app.query_one("#log", RichLog).lines)

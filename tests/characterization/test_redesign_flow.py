"""The new list uses the same journal for successful, refused and forced quits."""

import asyncio

import pytest

from travel_mode import state
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout
from tui_plan import app_item, apply_plan


async def test_default_start_reopens_active_session_without_scenario_picker(machine):
    from travel_mode import cli

    assert cli.main(["on", "--scenario", "away", "--yes", "--no-measure"]) == 0
    old = state.load_state()
    app = TravelApp(scenario="ask", measure=False)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        assert app.scenario == "away"
        assert app.view_mode == "session"
        assert not app.query("#scenario-picker")
        assert app.st["session"]["id"] == old["session"]["id"]


async def test_session_new_scenario_requires_choice_before_modifying(machine):
    from travel_mode import cli
    from travel_mode.failures import mutation
    from travel_mode.tui.conflict import SessionConflict

    assert cli.main(["on", "--scenario", "away", "--yes", "--no-measure"]) == 0
    old = state.load_state()
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        machine["fake"].calls.clear()
        await app.utility_action("b-new-plan")
        assert app.screen.query("#scenario-picker")
        await pilot.press("1", "enter")
        await asyncio.wait_for(app.new_plan_complete.wait(), 30)
        await app.wait_for_idle()
        assert app.scenario == "work" and app.view_mode == "plan"
        assert app.st["scenario"] == "away"
        await click_after_layout(pilot, "#b-quick")
        assert isinstance(app.screen, SessionConflict)
        assert not any(mutation(c) for c in machine["fake"].calls)
        app.screen.query_one("#conflict-cancel").focus()
        await pilot.press("enter")
        await app.session_decision_complete.wait()
        assert state.load_state() == old


@pytest.mark.parametrize("scenario", ["work", "away"])
async def test_plan_result_session_and_restore_use_existing_actions(machine, scenario):
    app = TravelApp(scenario=scenario, measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        await apply_plan(pilot, names={"Freeze Demo"}, action="freeze")
        assert app.view_mode == "result"
        row = app_item(app, "Freeze Demo")
        assert row["state"] == "done" and not row["operations"]
        assert state.load_state()["frozen_apps"]
        await click_after_layout(pilot, "#b-complete")
        assert app.view_mode == "session"
        await click_after_layout(pilot, "#b-off")
        assert app.is_running and not state._path().exists()


@pytest.mark.parametrize("scenario,expected", [("work", "failed"), ("away", "warning")])
async def test_refusal_has_only_state_appropriate_controls(machine, scenario, expected):
    machine["fake"].responses[("osascript",)] = CmdResult("", "User canceled. (-128)", 1)
    app = TravelApp(scenario=scenario, measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        await apply_plan(pilot, names={"Quit Demo"}, action="quit")
        row = app_item(app, "Quit Demo")
        assert row["state"] == expected
        if expected == "failed":
            assert row["operations"] == ["retry", "force"]
            await click_after_layout(pilot, "#force-" + row["id"])
            assert row["state"] == "done" and not row["operations"]
            assert app.last_batch == dict(ok=1, failed=0)
        else:
            assert row["operations"] == []
            assert not app.st.get("action_failures")
        assert app.is_running and app._exception is None


async def test_failed_freeze_retry_repeats_freeze_and_updates_result_count(machine):
    command = ("kill", "-STOP", "910002")
    machine["fake"].responses[command] = CmdResult("", "fixture stop denied", 1)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        await apply_plan(pilot, names={"Freeze Demo"}, action="freeze")
        row = app_item(app, "Freeze Demo")
        assert row["state"] == "failed" and "retry" in row["operations"]
        assert "fixture stop denied" in "\n".join(row["evidence"])
        assert '"rc": 1' in "\n".join(row["evidence"])
        machine["fake"].responses[command] = CmdResult("", "", 0)
        machine["fake"].calls.clear()
        await click_after_layout(pilot, "#retry-" + row["id"])
        assert command in machine["fake"].calls
        assert not any(c[0] == "osascript" for c in machine["fake"].calls)
        assert row["state"] == "done" and row["action"] == "freeze"
        assert app.last_batch == dict(ok=1, failed=0)

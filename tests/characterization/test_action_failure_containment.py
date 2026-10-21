"""Real flows and Actions against the sealed stateful command fixture."""

import json

import pytest

from travel_mode import cli, state
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout
from tui_plan import app_item, apply_plan, select_plan


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_refused_quit_does_not_abort_cli_or_later_agents(machine, scenario, capsys):
    machine["fake"].responses[("sudo", "kill")] = CmdResult("", "", 0)
    machine["fake"].responses[("osascript",)] = CmdResult("", "User canceled. (-128)", 1)
    assert cli.main(["on", "--scenario", scenario, "--yes", "--no-measure"]) == (
        1 if scenario == "work" else 0
    )
    output = capsys.readouterr()
    assert "Traceback" not in output.out + output.err
    snapshot = state.load_state()
    assert str(machine["plist"]) in snapshot["unloaded_svcs"]
    if scenario == "work":
        assert snapshot["action_failures"][0]["rc"] == 1
    else:
        assert not snapshot.get("action_failures")
        assert snapshot["session"]["actions"]["failed"] == 0
    log = machine["root"] / "travel-mode.log"
    assert "-128" in log.read_text()
    assert ("910001" in machine["processes"]) is (scenario == "work")
    assert cli.main(["off"]) == 0
    history = json.loads((machine["root"] / "history.jsonl").read_text().splitlines()[-1])
    if scenario == "work":
        assert history["failures"] and history["failures"][0]["rc"] == 1
    else:
        assert not history.get("failures") and history["actions"]["failed"] == 0


@pytest.mark.parametrize(
    "error", [ValueError("fixture bug"), state.StateError("fixture journal write failed")]
)
async def test_busy_error_keeps_interface_and_records_traceback(machine, monkeypatch, error):
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()

        def fail(action, context=None):
            raise error

        monkeypatch.setattr(app.executor, "execute", fail)
        await click_after_layout(pilot, "#ready-0")
        assert app._exception is None and app.is_running
        log = machine["root"] / "travel-mode.log"
        assert str(error) in log.read_text() and "Traceback" in log.read_text()
        assert "travel-mode.log" in str(app.query_one("#last-result").content)


@pytest.mark.parametrize("after_mutation", [False, True])
async def test_state_write_error_stops_remaining_modifications_without_exiting(
    machine, monkeypatch, after_mutation
):
    original = state.save_state
    modifications = []
    from travel_mode.actions.journal import preparing_command
    from travel_mode.system import shell

    runner = shell.get_runner()
    checking_intents = True

    def track(command, timeout=None, interactive=False):
        if (
            command[:2] == ["sudo", "pmset"] or command[:1] in (["osascript"], ["kill"], ["launchctl"])
        ) and command[:2] != ["launchctl", "list"]:
            if checking_intents:
                persisted = state.load_state()
                assert persisted is not None
                if command[0] == "osascript":
                    assert any(path in persisted["quit_apps"] for path in machine["paths"].values())
                elif command[:2] == ["kill", "-STOP"]:
                    assert any(str(row["pid"]) == command[-1] for row in persisted["frozen_pids"])
                else:
                    assert command in persisted["pending_commands"]
            modifications.append(command)
        return runner(command, timeout, interactive)

    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        await select_plan(app)

        def broken(st, *args, **kwargs):
            battery_command = (preparing_command.get() or [])[:3] == ["sudo", "pmset", "-b"]
            battery_changed = any(cmd[:3] == ["sudo", "pmset", "-b"] for cmd in modifications)
            fail_write = battery_changed if after_mutation else battery_command
            if fail_write:
                raise state.StateError("fixture write denied before pmset")
            return original(st, *args, **kwargs)

        shell.set_runner(track)
        monkeypatch.setattr(state, "save_state", broken)
        await click_after_layout(pilot, "#b-quick")
        assert app.is_running and app._exception is None
        # Other independent commands may already be in flight when storage
        # fails. Each must have its own durable intent; the affected command
        # must never run if its intent write failed.
        affected = [cmd for cmd in modifications if cmd[:3] == ["sudo", "pmset", "-b"]]
        assert len(affected) == int(after_mutation)
        assert "fixture write denied" in (machine["root"] / "travel-mode.log").read_text()
        assert state.load_state() is not None
        checking_intents = False
        monkeypatch.setattr(state, "save_state", original)
        assert cli.main(["off"]) == 0
        report = json.loads((machine["root"] / "history.jsonl").read_text().splitlines()[-1])
        assert any(row["kind"] == "state" for row in report["failures"])
        assert report["actions"]["failed"] >= 1


async def test_work_refusal_keeps_retry_force_controls_and_other_apps_continue(machine):
    machine["fake"].responses[("osascript",)] = CmdResult("", "User canceled. (-128)", 1)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(160, 65)) as pilot:
        await app.wait_for_idle()
        await apply_plan(pilot, group="apps")
        data = app_item(app, "Quit Demo")
        assert data["state"] == "failed" and data["app_running"]
        assert data["operations"] == ["retry", "force"]
        assert machine["processes"]["910002"][0] == "T"
        await click_after_layout(pilot, "#retry-" + data["id"])
        assert app.is_running and "910001" in machine["processes"]
        await click_after_layout(pilot, "#force-" + data["id"])
        assert app.is_running and "910001" not in machine["processes"]


async def test_unexpected_read_worker_error_is_logged_without_closing_interface(machine):
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(140, 55)):
        await app.wait_for_idle()

        async def bug():
            raise LookupError("fixture worker invariant")

        app.run_worker(bug(), group="fixture-error")
        await app.wait_for_idle()
        assert app.is_running and app._exception is None
        log = (machine["root"] / "travel-mode.log").read_text()
        assert "LookupError" in log and "Traceback" in log
        assert "travel-mode.log" in str(app.query_one("#last-result").content)


def test_restore_agent_observation_failure_is_logged_and_other_items_continue(machine):
    from travel_mode.flows.restore import run_restore
    from travel_mode.system import shell

    st = state.new_state()
    st["unloaded_svcs"] = [str(machine["plist"])]
    st["bt_off"] = "1"
    runner = shell.get_runner()

    def deny_list(command, timeout=None, interactive=False):
        if command == ["launchctl", "list"]:
            return CmdResult("", "fixture list denied", 1)
        return runner(command, timeout, interactive)

    shell.set_runner(deny_list)
    messages = []
    restored, failed = run_restore(st, lambda kind, text: messages.append((kind, text)))
    assert (restored, failed) == (1, 1)
    assert st["bt_off"] == "" and st["unloaded_svcs"]
    receipt = state.load_state()["action_failures"][-1]
    assert receipt["command"] == ["launchctl", "list"] and receipt["rc"] == 1
    assert any("travel-mode.log" in text for _, text in messages)


@pytest.mark.parametrize("interface", ["cli", "tui"])
async def test_away_both_quit_steps_fail_once_and_other_actions_continue(machine, interface):
    from travel_mode.system import shell

    from .test_failure_examples import configure

    configure(machine)
    runner = shell.get_runner()

    def deny_force(command, timeout=None, interactive=False):
        if command == ["kill", "-TERM", "910001"]:
            return CmdResult("", "fixture force denied", 2)
        return runner(command, timeout, interactive)

    shell.set_runner(deny_force)
    if interface == "cli":
        assert cli.main(["on", "--scenario", "away", "--yes", "--no-measure"]) == 1
    else:
        app = TravelApp(scenario="away", measure=False)
        async with app.run_test(size=(160, 65)) as pilot:
            await app.wait_for_idle()
            await click_after_layout(pilot, "#b-quick")
            assert app.is_running and app._exception is None
    snapshot = state.load_state()
    assert len(snapshot["action_failures"]) == 1
    reason = snapshot["action_failures"][0]["reason"]
    assert "(-128)" in reason and "fixture force denied" in reason
    assert snapshot["session"]["actions"]["failed"] == 1
    assert str(machine["plist"]) in snapshot["unloaded_svcs"]
    assert snapshot["frozen_apps"]
    assert cli.main(["off"]) == 0

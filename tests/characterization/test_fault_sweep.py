"""Enumerate the successful plan, then fail every mutation occurrence in isolation."""

import copy
import json
from types import SimpleNamespace

import pytest
from textual.widgets import RichLog

from travel_mode import cli, state
from travel_mode.system import apps, process_control, shell
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from tui_plan import apply_plan


def mutation(command):
    bare = [p for p in command if p not in {"sudo", "-n"}]
    if not bare:
        return False
    return (
        bare[0] in {"kill", "open", "osascript"}
        or (bare[0] == "pmset" and bare[1] != "-g")
        or (bare[0] == "launchctl" and bare[1] in {"load", "unload"})
        or (bare[0] == "brightness" and bare[1] != "-l")
        or bare[:2] == ["mdutil", "-i"]
        or bare[:2] == ["networksetup", "-setairportpower"]
        or (bare[0] == "blueutil" and len(bare) == 3)
    )


def independent(command, machine):
    """Signals for the same bundle belong to one action, not independent work."""
    bare = tuple(p for p in command if p not in {"sudo", "-n"})
    if bare[0] == "kill":
        original = machine["original_processes"].get(bare[-1])
        return ("app", original[1].split(".app/")[0]) if original else ("pid", bare[-1])
    if bare[0] == "osascript":
        return ("app", bare[4])
    return bare[:2]


@pytest.fixture
def sweep(machine, monkeypatch):
    original_runner = shell.get_runner()
    processes = copy.deepcopy(machine["processes"])
    loaded = set(machine["loaded"])
    responses = dict(machine["fake"].responses)
    monkeypatch.setattr(apps, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(process_control, "time", SimpleNamespace(sleep=lambda _: None))
    machine["original_processes"] = processes

    def reset(index=None, refusal=False):
        machine["processes"].clear()
        machine["processes"].update(copy.deepcopy(processes))
        machine["loaded"].clear()
        machine["loaded"].update(loaded)
        machine["fake"].responses.clear()
        machine["fake"].responses.update(responses)
        machine["fake"].responses[("sudo", "kill")] = CmdResult("", "", 0)
        machine["fake"].calls.clear()
        for name in ("state.json", "history.jsonl", "travel-mode.log", "travel-mode.log.1"):
            (machine["root"] / name).unlink(missing_ok=True)
        observed = []

        def runner(command, timeout=None, interactive=False):
            if mutation(command):
                observed.append(tuple(command))
                if len(observed) - 1 == index:
                    machine["fake"].calls.append(tuple(command))
                    return CmdResult("", "User canceled. (-128)" if refusal else "fixture mutation denied", 1)
            return original_runner(command, timeout, interactive)

        shell.set_runner(runner)
        return observed

    yield reset
    shell.set_runner(original_runner)


def verify_failure(machine, commands, plan, index):
    snapshot = state.load_state()
    assert snapshot is not None
    has_failure = bool(snapshot.get("action_failures"))
    assert (snapshot["session"]["actions"]["failed"] > 0) is has_failure
    assert snapshot["session"]["actions"]["attempted"] == sum(
        snapshot["session"]["actions"][key] for key in ("succeeded", "failed")
    )
    log = machine["root"] / "travel-mode.log"
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert any(row["rc"] == 1 for row in rows)
    if not has_failure:
        assert rows and all(row["kind"] == "warning" for row in rows)
    assert log.stat().st_mode & 0o777 == 0o600
    # Independent tool families after the failed item must still be attempted.
    # Follow-up signals within that failed action need not be sent.
    affected = independent(plan[index], machine)
    later = {independent(cmd, machine) for cmd in plan[index + 1 :]} - {affected}
    attempted = {independent(cmd, machine) for cmd in commands}
    assert later <= attempted, (plan[index], later - attempted, commands)
    # Disable the injection before recovery; only fixture commands can run.
    assert cli.main(["off"]) == 0
    assert state.load_state() is None
    report = json.loads((machine["root"] / "history.jsonl").read_text().splitlines()[-1])
    assert bool(report.get("failures")) is has_failure


@pytest.mark.parametrize("scenario", ["away", "work"])
@pytest.mark.parametrize("aggressive", [False, True])
def test_cli_every_planned_mutation_failure_continues_and_off_recovers(
    machine, sweep, scenario, aggressive, capsys
):
    argv = ["on", "--scenario", scenario, "--yes", "--no-measure"] + (["--aggressive"] if aggressive else [])
    plan = sweep()
    assert cli.main(argv) == 0
    plan = tuple(plan)
    capsys.readouterr()
    assert plan
    mutations = 0
    for index, command in enumerate(plan):
        for refusal in [False, True] if command[:1] == ("osascript",) else [False]:
            observed = sweep(index, refusal)
            assert cli.main(argv) in {0, 1}
            captured = capsys.readouterr()
            assert "Traceback" not in captured.out + captured.err
            verify_failure(machine, observed, plan, index)
            capsys.readouterr()
            mutations += 1
    with capsys.disabled():
        print(
            "FAULT_SWEEP "
            + json.dumps(
                dict(
                    interface="cli",
                    scenario=scenario,
                    aggressive=aggressive,
                    commands=len(plan),
                    injections=mutations,
                )
            )
        )


async def run_ui(scenario, page):
    app = TravelApp(scenario=scenario, measure=False)
    async with app.run_test(size=(160, 65)) as pilot:
        await app.wait_for_idle()
        await apply_plan(pilot, group=None if page == "quick" else page)
        assert app._exception is None and app.is_running
        text = "\n".join(str(line) for line in app.query_one("#log", RichLog).lines)
        assert "Traceback" not in text
        return text


@pytest.mark.parametrize("scenario", ["away", "work"])
@pytest.mark.parametrize("page", ["quick", "apps", "services", "network"])
async def test_tui_every_planned_mutation_failure_keeps_interface_and_off_recovers(
    machine, sweep, scenario, page, capsys
):
    plan = sweep()
    baseline = await run_ui(scenario, page)
    assert "travel-mode.log" not in baseline
    assert state.load_state()["session"]["actions"]["failed"] == 0
    plan = tuple(plan)
    assert plan
    capsys.readouterr()
    injections = 0
    for index, command in enumerate(plan):
        for refusal in [False, True] if command[:1] == ("osascript",) else [False]:
            observed = sweep(index, refusal)
            text = await run_ui(scenario, page)
            if state.load_state().get("action_failures"):
                assert "travel-mode.log" in text
            else:
                assert (machine["root"] / "travel-mode.log").exists()
            captured = capsys.readouterr()
            assert "Traceback" not in captured.out + captured.err
            verify_failure(machine, observed, plan, index)
            capsys.readouterr()
            injections += 1
    with capsys.disabled():
        print(
            "FAULT_SWEEP "
            + json.dumps(
                dict(interface="tui", scenario=scenario, page=page, commands=len(plan), injections=injections)
            )
        )


@pytest.mark.parametrize("scenario", ["away", "work"])
@pytest.mark.parametrize("interface", ["cli", "tui"])
async def test_each_privileged_plan_command_without_password_is_not_executed(
    machine, sweep, monkeypatch, scenario, interface, capsys
):
    from textual.widgets import Button

    from travel_mode.tui.administrator import AdministratorScreen

    original_push = TravelApp.push_screen

    def push(app, screen, *args, **kwargs):
        result = original_push(app, screen, *args, **kwargs)
        if isinstance(screen, AdministratorScreen):
            screen.call_after_refresh(lambda: screen.query_one("#admin-skip", Button).press())
        return result

    monkeypatch.setattr(TravelApp, "push_screen", push)
    if interface == "cli":
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    argv = ["on", "--scenario", scenario, "--yes", "--aggressive", "--no-measure"]
    plan = sweep()
    if interface == "cli":
        assert cli.main(argv) == 0
    else:
        await run_ui(scenario, "quick")
    plan = tuple(plan)
    privileged = list(dict.fromkeys(command for command in plan if command[:1] == ("sudo",)))
    assert privileged
    for command in privileged:
        observed = sweep()
        machine["fake"].responses[("sudo", "-n", "-l", *command[1:])] = CmdResult("", "password required", 1)
        if interface == "cli":
            assert cli.main(argv) == 1
        else:
            await run_ui(scenario, "quick")
        snapshot = state.load_state()
        assert snapshot is not None
        assert command not in observed
        receipts = snapshot["action_failures"]
        assert any(row["command"] == list(command) and row["kind"] == "not_executed" for row in receipts)
        assert snapshot["session"]["actions"]["failed"] > 0
        assert not any(c == ("sudo", "-v") for c in machine["fake"].calls)
        later = {independent(cmd, machine) for cmd in plan[plan.index(command) + 1 :]} - {
            independent(command, machine)
        }
        # A configured daemon must be stopped before quitting its owning app.
        # Permission failure for its query/unload makes that Quit dependent.
        if command[1:3] in (("launchctl", "unload"), ("launchctl", "list")):
            import plistlib
            from pathlib import Path

            from travel_mode.config import load_config

            for spec in load_config().apps.with_daemon:
                if command[2] == "list" or command[-1] in spec.system_daemons:
                    bundle = plistlib.loads(
                        (Path(machine["paths"][spec.name]) / "Contents/Info.plist").read_bytes()
                    )["CFBundleIdentifier"]
                    related = {
                        independent(c, machine)
                        for c in plan
                        if c[:1] == ("osascript",) and any(bundle in part for part in c)
                    }
                    later -= related
        assert later <= {independent(cmd, machine) for cmd in observed}
        machine["fake"].responses.pop(("sudo", "-n", "-l", *command[1:]))
        assert cli.main(["off"]) == 0
        output = capsys.readouterr()
        assert "Traceback" not in output.out + output.err
        assert "Password:" not in output.out + output.err

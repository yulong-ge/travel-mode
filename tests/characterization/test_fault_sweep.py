"""Exhaustive CLI/Executor faults; five Pilot paths cover admission and recovery."""

import json
from collections import Counter

import pytest
from textual.widgets import Button

from travel_mode import cli, failures, snapshots, state
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.actions.privilege_plan import action_requests
from travel_mode.config import load_config
from travel_mode.profiles import profile_scope
from travel_mode.system.sudo import Credentials, credential_scope
from travel_mode.tui.administrator import AdministratorScreen
from travel_mode.tui.app import TravelApp
from travel_mode.tui.plan import collect, execution_units
from tui_interaction import click_after_layout
from tui_plan import app_item, apply_plan

from .conftest import normalize
from .fault_support import independent, verify_failure
from .fault_support import sweep as sweep


def command_family(command):
    bare = [p for p in command if p not in {"sudo", "-n"}]
    parts = bare[:2] if bare[0] in {"pmset", "kill", "launchctl"} else bare[:1]
    return " ".join(parts) + (" (system)" if command[:2] == ("sudo", "launchctl") else "")


def execute_plan(scenario, page, sweep):
    """Use the production plan, Action factory and Executor, with no App/Pilot."""
    with profile_scope(load_config(), None, scenario=scenario):
        snapshot, merged = snapshots.snapshot_origin()
        assert not merged
        rows = collect(scenario, snapshot)["rows"]
        for row in rows:
            row["selected"] = page == "quick" or row["group"] == page
        state.save_state(snapshot)
        actions = [action for _, action in execution_units(rows, scenario, snapshot, state.save_state)]
        credentials = Credentials(interactive=False)
        with credential_scope(credentials), failures.batch():
            credentials.preflight(action_requests(actions))
            sweep.arm()
            Executor(Context(snapshot, state.save_state)).execute_all(actions)


def assert_password_failure(machine, plan, command, observed):
    snapshot = state.load_state()
    assert command not in observed
    assert any(
        r["command"] == list(command) and r["kind"] == "not_executed" and r["rc"] == 77
        for r in snapshot["action_failures"]
    )
    assert snapshot["session"]["actions"]["failed"] > 0
    assert not any(c == ("sudo", "-v") for c in machine["fake"].calls)
    later = {independent(c, machine) for c in plan[plan.index(command) + 1 :]} - {
        independent(command, machine)
    }
    # Failure of the daemon's prerequisite also blocks its dependent quit.
    if command[1:3] == ("launchctl", "unload"):
        later -= {
            independent(c, machine)
            for c in plan
            if c[0] == "osascript" and any("org.demo.app910005" in part for part in c)
        }
    assert later <= {independent(c, machine) for c in observed}


@pytest.mark.parametrize("scenario", ["away", "work"])
@pytest.mark.parametrize("page", ["quick", "apps", "services", "network"])
def test_plan_fault_families_preserve_recovery_without_pilot(machine, sweep, scenario, page, capsys):
    observed = sweep()
    execute_plan(scenario, page, sweep)
    plan = tuple(observed)
    # These are measured from the old Pilot driver, not inferred from the new runner.
    expected = {"quick": (12, 15), "apps": (2, 3), "services": (2, 2), "network": (5, 7)}[page]
    assert len(plan) == expected[0]
    assert len(plan) + sum(c[0] == "osascript" for c in plan) == expected[1]
    injected = {key: 0 for key in ("command", "refused", "password", "state", "internal")}
    by_family = {key: Counter() for key in injected}
    for index, command in enumerate(plan):
        families = ["command", "state", "internal"]
        if command[0] == "osascript":
            families.append("refused")
        if command[0] == "sudo":
            families.append("password")
        for family in families:
            observed = sweep(index, family=family, target=command)
            if family in {"state", "internal"}:
                error, message = (
                    (state.StateError, "fixture journal save denied")
                    if family == "state"
                    else (LookupError, "fixture runner invariant")
                )
                with pytest.raises(error, match=message):
                    execute_plan(scenario, page, sweep)
                assert sweep.triggered
                assert len(observed) <= index + int(family == "internal")
                logged = [
                    json.loads(line)
                    for line in (machine["root"] / "travel-mode.log").read_text().splitlines()
                ]
                assert logged[-1]["kind"] == family
                assert error.__name__ in logged[-1]["traceback"]
                sweep.disable()
                assert cli.main(["off"]) == 0
                assert state.load_state() is None
            else:
                execute_plan(scenario, page, sweep)
                sweep.disable()
                if family == "password":
                    assert_password_failure(machine, plan, command, observed)
                    assert cli.main(["off"]) == 0
                    assert state.load_state() is None
                else:
                    assert sweep.triggered
                    verify_failure(machine, observed, plan, index)
            injected[family] += 1
            by_family[family][command_family(command)] += 1
            capsys.readouterr()
    with capsys.disabled():
        print(
            "EXECUTOR_SWEEP "
            + json.dumps(
                dict(
                    scenario=scenario,
                    page=page,
                    commands=len(plan),
                    injections=injected,
                    families=by_family,
                    trace=normalize(plan, machine["root"]),
                )
            )
        )


@pytest.mark.parametrize("family", ["command", "refused", "password", "state", "internal"])
async def test_tui_fault_representative_admits_continues_retries_and_restores(
    machine, sweep, monkeypatch, family
):
    baseline = sweep()
    execute_plan("work", "quick", sweep)
    plan = tuple(baseline)
    if family == "password":
        index = next(i for i, c in enumerate(plan) if c[:2] == ("sudo", "pmset"))
    elif family in {"command", "refused"}:
        index = next(i for i, c in enumerate(plan) if c[0] == "osascript")
    else:
        index = next(i for i, c in enumerate(plan) if c[:2] == ("kill", "-STOP"))
    target = plan[index]
    sweep(index, family=family, target=target)
    pushed = TravelApp.push_screen

    def skip_password(app, screen, *args, **kwargs):
        result = pushed(app, screen, *args, **kwargs)
        if isinstance(screen, AdministratorScreen):
            screen.call_after_refresh(lambda: screen.query_one("#admin-skip", Button).press())
        return result

    monkeypatch.setattr(TravelApp, "push_screen", skip_password)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(160, 65)) as pilot:
        await app.wait_for_idle()
        sweep.arm()
        await apply_plan(pilot)
        assert app.is_running and app._exception is None and not app._mutation_lock.locked()
        snapshot = state.load_state()
        if family == "password":
            assert any(r["kind"] == "not_executed" and r["rc"] == 77 for r in snapshot["action_failures"])
            assert target not in sweep.observed
            assert not any(c == ("sudo", "-v") for c in machine["fake"].calls)
        else:
            assert sweep.triggered
        if family in {"command", "refused", "password"}:
            assert str(machine["plist"]) in snapshot["unloaded_svcs"]
        logged = [json.loads(line) for line in (machine["root"] / "travel-mode.log").read_text().splitlines()]
        expected_kind = {"command": "action", "refused": "action", "password": "not_executed"}.get(
            family, family
        )
        assert any(r["kind"] == expected_kind for r in logged)
        if family == "refused":
            assert snapshot["action_failures"][0]["quit_status"] == "refused"
        if family in {"state", "internal"}:
            assert len(sweep.observed) <= index + int(family == "internal")
        sweep.disable()
        if family in {"command", "refused"}:
            row = app_item(app, "Quit Demo")
            assert row["state"] == "failed"
            assert row["operations"] == ["retry", "force"]
            assert row["retry_action"] == "quit"
            await click_after_layout(pilot, "#retry-" + row["id"])
            assert row["state"] == "done" and row["operations"] == []
            assert any(r.get("recovered") for r in state.load_state()["action_failures"])
        await click_after_layout(pilot, "#b-complete")
        assert app.view_mode == "session"
        await click_after_layout(pilot, "#b-off")
        await app.wait_for_idle()
        assert state.load_state() is None
        assert app.is_running and app._exception is None
        report = json.loads((machine["root"] / "history.jsonl").read_text().splitlines()[-1])
        assert report["restore"]["failed"] == 0
        assert any(r["kind"] == expected_kind for r in report["failures"])


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


@pytest.mark.parametrize("scenario", ["away", "work"])
def test_cli_each_privileged_command_without_password_is_not_executed(machine, sweep, scenario, capsys):
    argv = ["on", "--scenario", scenario, "--yes", "--aggressive", "--no-measure"]
    observed = sweep()
    assert cli.main(argv) == 0
    plan = tuple(observed)
    privileged = list(dict.fromkeys(c for c in plan if c[0] == "sudo"))
    assert privileged
    for command in privileged:
        observed = sweep(family="password", target=command)
        assert cli.main(argv) == 1
        sweep.disable()
        assert_password_failure(machine, plan, command, observed)
        machine["fake"].responses.pop(("sudo", "-n", "-l", *command[1:]))
        assert cli.main(["off"]) == 0
        assert state.load_state() is None
        capsys.readouterr()

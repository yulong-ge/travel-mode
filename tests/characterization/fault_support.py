"""Stateful fault injection shared by CLI, Executor and representative Pilot paths."""

import copy
import json
from types import SimpleNamespace

import pytest

from travel_mode import cli, state
from travel_mode.system import apps, process_control, shell
from travel_mode.system.shell import CmdResult


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
    original_save = state.save_state
    active = {}

    def save(snapshot, *args, **kwargs):
        if active.get("family") == "state" and active.get("armed") and len(reset.observed) == active["index"]:
            reset.triggered = True
            raise state.StateError("fixture journal save denied")
        return original_save(snapshot, *args, **kwargs)

    monkeypatch.setattr(state, "save_state", save)

    def reset(index=None, refusal=False, *, family=None, target=None):
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
        reset.observed, reset.triggered = observed, False
        active.update(index=index, family=family or ("refused" if refusal else "command"), armed=False)
        if family == "password":
            assert target[0] == "sudo"
            machine["fake"].responses[("sudo", "-n", "-l", *target[1:])] = CmdResult(
                "", "password required", 1
            )

        def runner(command, timeout=None, interactive=False):
            if mutation(command):
                observed.append(tuple(command))
                if len(observed) - 1 == active["index"] and active["family"] in {
                    "command",
                    "refused",
                    "internal",
                }:
                    reset.triggered = True
                    machine["fake"].calls.append(tuple(command))
                    if active["family"] == "internal":
                        raise LookupError("fixture runner invariant")
                    return CmdResult(
                        "",
                        "User canceled. (-128)"
                        if active["family"] == "refused"
                        else "fixture mutation denied",
                        1,
                    )
            return original_runner(command, timeout, interactive)

        shell.set_runner(runner)
        return observed

    reset.arm = lambda: active.update(armed=True)
    reset.disable = lambda: active.update(index=None, family=None, armed=False)
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

"""Event delivery harness guards one independent agent, never the product label."""

import os
import plistlib
from pathlib import Path

import pytest
from e2e_automation import TEST_LABEL, EventGuard, GuardViolation, main

from conftest import FakeRunner
from travel_mode import automation
from travel_mode.system.shell import CmdResult


def test_event_e2e_plan_lists_delivery_idle_and_exact_cleanup(capsys):
    assert main(["--plan"]) == 0
    output = capsys.readouterr().out
    assert TEST_LABEL in output and "bootstrap" in output and "bootout" in output
    assert "pmset -g ps" in output and "on_battery=" in output
    assert "--launch-event" in output and "LaunchEvents" in output
    assert "Manually unplug" in output and "no running process" in output
    assert "KeepAlive" not in output and "RunAtLoad" not in output


@pytest.mark.parametrize(
    "command",
    [
        ["sudo", "pmset", "-b", "lowpowermode", "1"],
        ["launchctl", "bootout", "gui/501/other"],
        ["launchctl", "bootstrap", "gui/501", "/other.plist"],
        ["notifyutil", "-p", "com.apple.system.powersources.source"],
        ["osascript", "-e", 'display notification "test"'],
        ["launchctl", "print", "gui/501/" + automation.LABEL],
    ],
)
def test_event_guard_rejects_every_non_fixture_mutation(tmp_path, command):
    backend = FakeRunner()
    guard = EventGuard(backend, tmp_path / "test.plist", tmp_path / "helper", uid=501)
    with pytest.raises(GuardViolation):
        guard(command)
    assert backend.calls == []


def test_event_guard_bootstrap_requires_exact_owned_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(automation, "LABEL", TEST_LABEL)
    path, helper = tmp_path / "test.plist", tmp_path / "helper"
    command = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]
    backend = FakeRunner({tuple(command): CmdResult("", "", 0)})
    guard = EventGuard(backend, path, helper)
    data = automation.agent_plist(str(helper))
    path.write_bytes(plistlib.dumps(data))
    assert guard(command).rc == 0
    data["KeepAlive"] = True
    path.write_bytes(plistlib.dumps(data))
    with pytest.raises(GuardViolation):
        guard(command)
    assert backend.calls == [tuple(command)]


def test_child_guard_cannot_install_or_notify(tmp_path):
    backend = FakeRunner({("pmset", "-g", "ps"): CmdResult("Battery Power", "", 0)})
    path = tmp_path / "test.plist"
    guard = EventGuard(backend, path, tmp_path / "helper", child=True)
    assert guard(["pmset", "-g", "ps"]).stdout == "Battery Power"
    with pytest.raises(GuardViolation):
        guard(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
    assert backend.calls == [("pmset", "-g", "ps")]


@pytest.mark.parametrize("delivered", [False, True])
def test_event_delivery_harness_requires_new_source_and_always_cleans_owned_agent(
    tmp_paths, darwin_platform, monkeypatch, delivered
):
    from e2e_automation import run

    from travel_mode.system import shell

    source = ["Battery Power"]
    commands = []
    loaded = [False]

    def backend(command, timeout=None, interactive=False):
        commands.append(command)
        if command[:2] == ["launchctl", "print"]:
            return CmdResult("state = not running", "", 0) if loaded[0] else CmdResult("", "missing", 113)
        if command[:2] == ["launchctl", "bootstrap"]:
            loaded[0] = True
        elif command[:2] == ["launchctl", "bootout"]:
            loaded[0] = False
        elif command == ["pmset", "-g", "ps"]:
            return CmdResult(source[0], "", 0)
        else:
            pytest.fail("Unexpected harness command: " + repr(command))
        return CmdResult("", "", 0)

    def confirm(prompt):
        import time

        if delivered:
            source[0] = "AC Power"
            automation.append_log({"at": time.time(), "source": "ac", "result": "handled", "details": []}, 50)
        return ""

    monkeypatch.setattr(shell, "subprocess_runner", backend)
    monkeypatch.setattr("builtins.input", confirm)
    if delivered:
        assert run() == 0
    else:
        with pytest.raises(AssertionError, match="Event delivery"):
            run()
    assert not loaded[0]
    assert not (Path.home() / "Library/LaunchAgents" / (TEST_LABEL + ".plist")).exists()
    assert all(TEST_LABEL in command[-1] for command in commands if command[0] == "launchctl")

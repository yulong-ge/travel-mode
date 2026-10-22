"""Cold compilation has a separate budget; cleanup must preserve the primary error."""

import json
from types import SimpleNamespace

import pytest
from e2e_guard import LSREGISTER
from e2e_refusal import COMPILE_TIMEOUT, compile_command, create
from e2e_ui import SETUP_TIMEOUT, InteractiveHarness, UISession, child
from harness_evidence import write_json

from travel_mode.system.shell import CmdResult


def test_setup_budget_covers_cold_compile_signing_and_launch(tmp_path, monkeypatch):
    import e2e_ui

    session = object.__new__(UISession)
    session.root = tmp_path
    session.timeout = 40
    from e2e_graceful import EVENT_SECONDS, EXIT_SECONDS, START_SECONDS

    assert SETUP_TIMEOUT >= COMPILE_TIMEOUT + 30 + 2 * START_SECONDS + EVENT_SECONDS + 5 + EXIT_SECONDS

    def wait(predicate, message, timeout):
        assert timeout == SETUP_TIMEOUT > session.timeout
        write_json(tmp_path / "capabilities.json", {"fixture_only_power": False, "sleep_switch": True})
        return predicate()

    monkeypatch.setattr(e2e_ui, "wait_for", wait)
    assert session.wait_fixture_setup()


def test_setup_failure_surfaces_child_error_without_waiting_for_prompt(tmp_path):
    session = object.__new__(UISession)
    session.root = tmp_path
    write_json(tmp_path / "result.json", {"status": "FAIL", "error": "compile failed: fixture diagnostic"})
    with pytest.raises(AssertionError, match="compile failed: fixture diagnostic"):
        session.wait_fixture_setup()


def test_tui_on_failure_reaches_driver_and_child_before_off(tmp_path, monkeypatch):
    from harness_evidence import EvidenceError, acknowledged

    harness = object.__new__(InteractiveHarness)
    failure = AssertionError("battery target not applied")

    def assert_on():
        raise failure

    monkeypatch.setattr(harness, "assert_on", assert_on)
    exited = []
    app = SimpleNamespace(on_assertion_error=None, exit=lambda: exited.append(True))
    harness.verify_tui_on(app, tmp_path)
    assert app.on_assertion_error is failure and exited == [True]
    with pytest.raises(EvidenceError, match="battery target not applied"):
        acknowledged(tmp_path / "on.json")


def test_partial_refusing_bundle_not_unregistered_and_build_output_saved(sandbox, tmp_path, monkeypatch):
    harness, guard, machine = sandbox
    original = type(machine).__call__

    def invoke(self, command, *args, **kwargs):
        if command == compile_command(harness.root):
            return CmdResult("compiler output", "compiler error", 1)
        return original(self, command, *args, **kwargs)

    monkeypatch.setattr(type(machine), "__call__", invoke)
    with pytest.raises(RuntimeError, match="compilation failed"):
        create(harness.root, guard, evidence=tmp_path)
    stages = json.loads((tmp_path / "refusing-build.json").read_text())["stages"]
    assert stages[0]["stdout"] == "compiler output" and stages[0]["stderr"] == "compiler error"
    harness.cleanup()
    assert (LSREGISTER, "-u", str(guard.refusing)) not in guard.commands


def test_child_keeps_setup_failure_primary_when_cleanup_also_fails(tmp_path, monkeypatch, darwin_platform):
    root = tmp_path / "travel-mode-e2e-startup"
    root.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    def setup(self):
        raise RuntimeError("original build failure")

    def cleanup(self):
        raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(InteractiveHarness, "setup", setup)
    monkeypatch.setattr(InteractiveHarness, "cleanup", cleanup)
    args = SimpleNamespace(
        root=root,
        demo=True,
        demo_vm=False,
        battery_measure=False,
        evidence=evidence,
        real_state=tmp_path / "unused-state",
        child="cli",
        scenario="work",
    )
    with pytest.raises(RuntimeError, match="original build failure"):
        child(args)
    result = json.loads((evidence / "result.json").read_text())
    assert "original build failure" in result["error"]
    assert "original build failure" in result["primary_error"]
    assert "secondary cleanup failure" in result["cleanup_error"]


def test_failure_capture_saves_complete_terminal_scrollback(tmp_path, monkeypatch):
    session = object.__new__(UISession)
    session.root, session.cols, session.rows, session.captures = tmp_path, 120, 36, []
    calls = []

    def tmux(*command):
        calls.append(command)
        return "first compiler message\nlast error" if "-S" in command else "last error"

    monkeypatch.setattr(session, "tmux", tmux)
    monkeypatch.setattr("e2e_ui.render", lambda *args: None)
    session.shot("failure")
    assert "first compiler message" in (tmp_path / "failure-terminal.txt").read_text()
    assert "first compiler message" in (tmp_path / "failure-terminal.ansi").read_text()


def test_real_compiler_boundary_keeps_partial_output_on_interrupt(sandbox, tmp_path, monkeypatch):
    from e2e_guard import LocalBackend

    from travel_mode.system import shell

    harness, guard, _machine = sandbox
    backend = LocalBackend([])
    monkeypatch.setattr(shell, "_is_executable", lambda _: True)

    def compiler(command, **kwargs):
        assert command[0] == "/usr/bin/swiftc" and kwargs["timeout"] == COMPILE_TIMEOUT
        kwargs["stdout"].write("partial compiler output\n")
        kwargs["stderr"].write("partial compiler diagnostic\n")
        raise KeyboardInterrupt

    monkeypatch.setattr("e2e_guard.subprocess.run", compiler)
    guard.backend = backend
    with pytest.raises(KeyboardInterrupt):
        create(harness.root, guard, evidence=tmp_path)
    stage = json.loads((tmp_path / "refusing-build.json").read_text())["stages"][0]
    assert stage["stdout"] == "partial compiler output\n"
    assert stage["stderr"] == "partial compiler diagnostic\n"
    assert stage["status"] == "interrupted"
    assert "KeyboardInterrupt" in guard.trace[-1]["error"]

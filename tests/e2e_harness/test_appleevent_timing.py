"""VM capability timing uses acknowledgements; product deadlines stay independent."""

import json
from pathlib import Path

import e2e_graceful
import pytest
from e2e_fake import InstantClock
from e2e_guard import PROCESS_QUERY, QUIT_EVENTS
from e2e_local import product_environment

from travel_mode.system import apps
from travel_mode.system.shell import CmdResult


def setup_clock(harness, tmp_path):
    clock = InstantClock()
    harness.clock, harness.pause = clock.monotonic, clock.sleep
    harness.evidence = tmp_path / "evidence"
    harness.evidence.mkdir()
    return clock


def test_slow_vm_waits_for_idle_and_real_exit_without_resending_quit(sandbox, tmp_path):
    harness, guard, machine = sandbox
    clock = setup_clock(harness, tmp_path)
    ready_due, exit_due = {}, {}

    def backend(command, timeout=None, interactive=False):
        if command == PROCESS_QUERY:
            for app, deadline in list(ready_due.items()):
                if clock.now >= deadline:
                    e2e_graceful.receipts(app)[0].write_text("ready")
                    del ready_due[app]
            for pid, deadline in list(exit_due.items()):
                if clock.now >= deadline:
                    machine.rows.pop(pid, None)
                    del exit_due[pid]
        if command in [e2e_graceful.quit_command(app) for app in guard.graceful_apps]:
            app = next(app for app in guard.graceful_apps if command == e2e_graceful.quit_command(app))
            index = guard.graceful_apps.index(app)
            held = {pid: row for pid, row in machine.rows.items() if row[2].startswith(str(app) + "/")}
            result = machine(command, timeout, interactive)
            machine.rows.update(held)
            exit_due.update({pid: clock.now + (12 if index else 5) for pid in held})
            assert timeout == 35
            return result
        result = machine(command, timeout, interactive)
        if command[:3] == ["open", "-g", "-j"]:
            app = Path(command[-1])
            if app in guard.graceful_apps:
                e2e_graceful.receipts(app)[0].unlink()
                ready_due[app] = clock.now + 6
        return result

    guard.backend = backend
    with product_environment(harness.root, guard):
        assert harness.graceful_capability()
    assert clock.now >= 24
    events = [call for call in machine.calls if call[0] == "osascript"]
    assert len(events) == 2
    assert all("with timeout of 30 seconds" in event for event in events)
    evidence = json.loads((harness.evidence / "appleevent-capability.json").read_text())
    assert [row["phase"] for row in evidence["observations"]] == [
        "capability-start",
        "capability-quit-before",
        "capability-quit-sent",
        "capability-quit",
        "capability-reopen",
    ]
    assert len(evidence["observations"][2]["processes"]) == 2  # Reply arrived before actual exit.
    assert evidence["observations"][3]["processes"] == {}


@pytest.mark.parametrize("result", [CmdResult("", "", 0), CmdResult("", "AppleEvent timed out (-1712)", 1)])
def test_live_process_is_capability_failure_even_after_successful_reply_or_timeout(sandbox, tmp_path, result):
    harness, guard, machine = sandbox
    clock = setup_clock(harness, tmp_path)

    def backend(command, timeout=None, interactive=False):
        if command in [e2e_graceful.quit_command(app) for app in guard.graceful_apps]:
            machine.calls.append(tuple(command))
            return result
        return machine(command, timeout, interactive)

    guard.backend = backend
    with product_environment(harness.root, guard), pytest.raises(AssertionError, match="HARNESS CAPABILITY"):
        harness.graceful_capability()
    assert clock.now == 30
    events = [call for call in machine.calls if call[0] == "osascript"]
    assert len(events) == 2  # No retry, whether the transport succeeded or timed out.
    evidence = json.loads((harness.evidence / "appleevent-capability.json").read_text())
    assert evidence["observations"][-1]["phase"] == "capability-quit-timeout"
    assert len(evidence["observations"][-1]["processes"]) == 2
    assert [row["rc"] for row in evidence["observations"][-2]["results"]] == [result.rc] * 2


def test_product_timeout_is_not_skipped_or_given_capability_deadline(sandbox, tmp_path, monkeypatch, capsys):
    harness, guard, machine = sandbox
    clock = setup_clock(harness, tmp_path)
    monkeypatch.setattr(apps, "QuitClock", lambda: clock)

    def backend(command, timeout=None, interactive=False):
        if command in QUIT_EVENTS:
            machine.calls.append(tuple(command))
            assert timeout == 10
            return CmdResult("", "AppleEvent timed out (-1712)", 1)
        return machine(command, timeout, interactive)

    guard.backend = backend
    with product_environment(harness.root, guard), pytest.raises(AssertionError, match="PRODUCT"):
        harness.graceful()
    assert "SKIP graceful" not in capsys.readouterr().out
    assert len([call for call in machine.calls if list(call) in QUIT_EVENTS]) == 2

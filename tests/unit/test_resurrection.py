"""Recorded targets are observed once and remediated through existing Actions."""

import plistlib

import pytest

from travel_mode import resurrection, state
from travel_mode.actions.apps import FreezeApp, QuitApp, UnloadAgent
from travel_mode.system import process_control
from travel_mode.system.shell import CmdResult


def test_detect_reloaded_idle_agent_restarted_app_and_resumed_exact_pid(tmp_paths, fake_runner):
    path = tmp_paths / "agent.plist"
    path.write_bytes(plistlib.dumps({"Label": "org.example.backup"}))
    fake_runner.responses[("launchctl", "list")] = CmdResult("- 0 org.example.backup", "", 0)
    command = "/Applications/Fixture.app/Contents/MacOS/Fixture"
    fake_runner.responses[("ps",)] = CmdResult(f"910001 1 S Thu Oct 15 12:00:00 2026 {command}", "", 0)
    snapshot = state.new_state()
    snapshot.update(
        unloaded_svcs=[str(path)],
        quit_apps=["/Applications/Fixture.app"],
        frozen_pids=[
            {
                "pid": 910001,
                "started": "Thu Oct 15 12:00:00 2026",
                "command": command,
                "path": "/Applications/Fixture.app",
            }
        ],
    )
    snapshot["confirmed_pauses"] = [
        dict(kind="agent", path=str(path)),
        dict(kind="app", path="/Applications/Fixture.app"),
        dict(kind="pid", **snapshot["frozen_pids"][0]),
    ]
    rows = resurrection.detect(snapshot)
    assert [row["kind"] for row in rows] == ["agent", "app", "pid"]
    assert fake_runner.calls == [("launchctl", "list"), ("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")]
    actions = []

    def execute(action):
        actions.append(type(action))
        return ("", 0) if isinstance(action, UnloadAgent) else True

    saved = []
    resurrection.reapply(snapshot, execute, lambda value: saved.append(value))
    assert actions == [UnloadAgent, QuitApp, FreezeApp]
    assert len(saved) == 3


def test_frozen_pid_reuse_and_stopped_process_are_not_resurrections(monkeypatch):
    receipt = {
        "pid": 910001,
        "started": "old",
        "command": "/Applications/Fixture.app/Contents/MacOS/Fixture",
        "path": "/Applications/Fixture.app",
    }
    for row in [
        process_control.Process(910001, 1, "S", "new", receipt["command"]),
        process_control.Process(910001, 1, "T", "old", receipt["command"]),
    ]:
        monkeypatch.setattr(process_control, "process_table", lambda row=row: [row])
        assert (
            resurrection.detect({"frozen_pids": [receipt], "confirmed_pauses": [dict(kind="pid", **receipt)]})
            == []
        )


def test_no_recorded_pauses_performs_no_probes(fake_runner):
    assert resurrection.detect(state.new_state()) == []
    assert not fake_runner.calls


@pytest.mark.parametrize("kind", ["app", "pid", "agent"])
def test_recovery_intent_without_success_is_not_a_resurrection(kind, tmp_paths, fake_runner):
    path = tmp_paths / "not-executed.plist"
    path.write_bytes(plistlib.dumps({"Label": "org.fixture.not-executed"}))
    target = "/Applications/NotPaused.app"
    birth = "Thu Oct 15 12:00:00 2026"
    receipt = dict(pid=910002, started=birth, command=target + "/Contents/MacOS/Main", path=target)
    st = state.new_state()
    st.update(quit_apps=[target], frozen_pids=[receipt], unloaded_svcs=[str(path)])
    # An unrelated success cannot make this failed/unexecuted intent a success.
    st["confirmed_pauses"] = [dict(kind="app", path="/Applications/Other.app")]
    fake_runner.responses[("ps",)] = CmdResult(f"910002 1 S {birth} {receipt['command']}", "", 0)
    fake_runner.responses[("launchctl", "list")] = CmdResult("- 0 org.fixture.not-executed", "", 0)
    if kind != "app":
        st["quit_apps"] = []
    if kind != "pid":
        st["frozen_pids"] = []
    if kind != "agent":
        st["unloaded_svcs"] = []
    assert resurrection.detect(st) == []

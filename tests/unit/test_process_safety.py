"""Process control regressions use an in-memory process table only."""

import os
import re
import time

import pytest

import travel_mode.flows.restore as flow_restore
import travel_mode.flows.thaw as flow_thaw
from travel_mode.system import apps as system_apps
from travel_mode.system import processes
from travel_mode.system.shell import CmdResult, set_runner


class ProcessRunner:
    def __init__(self, rows, fail_pid=None):
        self.rows = rows
        self.fail_pid = fail_pid
        self.calls = []

    def __call__(self, cmd, timeout=None, interactive=False):
        self.calls.append(tuple(cmd))
        if cmd == ["lsappinfo", "front"]:
            return CmdResult("ASN:0x0-0x1:", "", 0)
        if cmd[:2] == ["lsappinfo", "info"]:
            return CmdResult("pid = 900000", "", 0)
        if cmd[0] == "ps":
            return CmdResult(
                "\n".join(
                    f"{pid} {row.get('ppid', 1)} {row['stat']} Wed Oct 14 08:00:00 2026 {row['comm']}"
                    for pid, row in self.rows.items()
                    if row["stat"] != "gone"
                ),
                "",
                0,
            )
        if cmd[0] == "pgrep":
            hits = [
                f"{pid} {row.get('argv', row['comm'])}"
                for pid, row in self.rows.items()
                if row["stat"] != "gone" and re.search(cmd[-1], row.get("argv", row["comm"]))
            ]
            return CmdResult("\n".join(hits), "", 0 if hits else 1)
        if cmd[0] in {"kill", "pkill"}:
            if cmd[0] == "kill":
                pids = [int(cmd[-1])]
            else:
                pids = [
                    pid for pid, row in self.rows.items() if re.search(cmd[-1], row.get("argv", row["comm"]))
                ]
            if self.fail_pid in pids:
                return CmdResult("", "permission denied", 2)
            for pid in pids:
                if "-STOP" in cmd:
                    self.rows[pid]["stat"] = "T"
                elif "-CONT" in cmd:
                    self.rows[pid]["stat"] = "S"
                else:
                    self.rows[pid]["stat"] = "gone"
            return CmdResult("", "", 0 if pids else 1)
        if cmd[0] == "osascript":
            for row in self.rows.values():
                row["stat"] = "gone"
            return CmdResult("", "", 0)
        if cmd[-2:] == ["launchctl", "list"]:
            return CmdResult("", "", 0)
        pytest.fail(f"unexpected fake process command: {cmd!r}")


def _row(comm, stat="S", **extras):
    return dict(comm=comm, stat=stat, **extras)


def test_force_kill_targets_executable_not_regex_or_arguments(monkeypatch):
    path = "/Applications/Foo.app"
    rows = {
        801: _row(path + "/Contents/MacOS/Foo"),
        802: _row("/Applications/FooXapp/Contents/MacOS/Other"),
        803: _row("/bin/observer", argv="/bin/observer --watch " + path + "/Contents/MacOS/Foo"),
        804: _row("/Applications/Foo.app Helper/Contents/MacOS/Other"),
    }
    set_runner(ProcessRunner(rows))
    monkeypatch.setattr(time, "sleep", lambda _: None)
    assert system_apps.force_kill(path)
    assert rows[801]["stat"] == "gone"
    assert all(rows[pid]["stat"] == "S" for pid in (802, 803, 804))


@pytest.mark.parametrize(
    "path,pid",
    [
        ("/System/Applications/Utilities/Terminal.app", 811),
        ("/Applications/iTerm.app", 812),
        ("/Applications/Own.app", os.getpid()),
    ],
    ids=["terminal", "iterm", "self"],
)
def test_freeze_rejects_terminal_system_and_self(path, pid):
    runner = ProcessRunner({pid: _row(path + "/Contents/MacOS/Main")})
    set_runner(runner)
    with pytest.raises(PermissionError):
        system_apps.freeze_app(path)
    assert not any(command[0] in {"kill", "pkill"} for command in runner.calls)


def test_freeze_retains_receipts_after_partial_stop_failure(tmp_path):
    from travel_mode.actions.journal import mutation_scope
    from travel_mode.state import load_state, new_state, save_state

    path = "/Applications/Work.app"
    runner = ProcessRunner(
        {821: _row(path + "/Contents/MacOS/Main"), 822: _row(path + "/Contents/Frameworks/Helper")},
        fail_pid=822,
    )
    set_runner(runner)
    state = new_state()
    target = tmp_path / "checkpoint.json"
    with (
        mutation_scope(state, lambda value: save_state(value, target)),
        pytest.raises(RuntimeError, match="permission denied"),
    ):
        system_apps.freeze_app(path)
    assert runner.rows[821]["stat"] == "T"
    assert {row["pid"] for row in load_state(target)["frozen_pids"]} >= {821}


def test_freeze_and_thaw_preserve_preexisting_stopped_pid(tmp_path):
    from travel_mode.actions.journal import mutation_scope
    from travel_mode.state import new_state, save_state

    path = "/Applications/Work.app"
    runner = ProcessRunner(
        {831: _row(path + "/Contents/MacOS/Main"), 832: _row(path + "/Contents/Helpers/AlreadyStopped", "T")}
    )
    set_runner(runner)
    state = new_state()
    with mutation_scope(state, lambda value: save_state(value, tmp_path / "checkpoint.json")):
        assert system_apps.freeze_app(path)
    if path not in state["frozen_apps"]:
        state["frozen_apps"].append(path)
    assert flow_restore.run_restore(state, lambda *_: None)[1] == 0
    assert runner.rows[831]["stat"] == "S"
    assert runner.rows[832]["stat"] == "T"


def test_grace_quit_escapes_applescript_source(monkeypatch):
    path = "/Applications/Quoted.app"
    runner = ProcessRunner({841: _row(path + "/Contents/MacOS/Main")})
    set_runner(runner)
    monkeypatch.setattr(system_apps, "app_bid", lambda _: "")
    name = 'Demo" to quit\ndo shell script "unexpected"\n--'
    assert system_apps.grace_quit(path, name)
    script = next(command[4] for command in runner.calls if command[0] == "osascript")
    assert "\n" not in script
    assert '\\"' in script


def test_pgrep_probe_failure_is_explicit():
    set_runner(lambda *args, **kwargs: CmdResult("", "permission denied", 2))
    with pytest.raises(RuntimeError, match="permission denied"):
        processes.pgrep_lines("Work")


def test_freeze_rejects_ancestor_bundle():
    path = "/Applications/Ancestor.app"
    runner = ProcessRunner({os.getppid(): _row(path + "/Contents/MacOS/Parent")})
    set_runner(runner)
    with pytest.raises(PermissionError):
        system_apps.freeze_app(path)
    assert not any(command[0] in {"kill", "pkill"} for command in runner.calls)


def test_restore_does_not_resume_reused_pid():
    path = "/Applications/Work.app"
    runner = ProcessRunner({851: _row(path + "/Contents/MacOS/Replacement", "T")})
    set_runner(runner)
    state = {
        "frozen_apps": [path],
        "frozen_pids": [
            dict(
                pid=851,
                started="Tue Oct 13 08:00:00 2026",
                command=path + "/Contents/MacOS/Replacement",
                path=path,
            )
        ],
    }
    assert flow_restore.run_restore(state, lambda *_: None)[1] == 0
    assert runner.rows[851]["stat"] == "T"
    assert not any(command[0] in {"kill", "pkill"} for command in runner.calls)


def test_restore_resumes_recorded_helper_without_main_process():
    path = "/Applications/Work.app"
    runner = ProcessRunner({861: _row(path + "/Contents/Frameworks/Helper", "T")})
    set_runner(runner)
    state = {
        "frozen_apps": [path],
        "frozen_pids": [
            dict(
                pid=861,
                started="Wed Oct 14 08:00:00 2026",
                command=path + "/Contents/Frameworks/Helper",
                path=path,
            )
        ],
    }
    assert flow_restore.run_restore(state, lambda *_: None)[1] == 0
    assert runner.rows[861]["stat"] == "S"
    assert not any(command[0] == "open" for command in runner.calls)


def test_daemon_residual_root_process_uses_exact_privileged_pid(tmp_path, monkeypatch):
    import plistlib

    from travel_mode.config import DaemonApp
    from travel_mode.system import daemon_apps

    path = str(tmp_path / "Remote.app")
    plist = tmp_path / "daemon.plist"
    plist.write_bytes(plistlib.dumps({"Label": "com.example.remote"}))
    table = ProcessRunner({871: _row(path + "/Contents/Helpers/RootHelper")})
    calls = []

    def runner(cmd, timeout=None, interactive=False):
        calls.append(tuple(cmd))
        privileged = cmd[0] == "sudo"
        inner = cmd[1:] if privileged else cmd
        if inner == ["launchctl", "list"]:
            return CmdResult("- 0 com.example.remote\n", "", 0)
        if inner[:2] == ["launchctl", "unload"]:
            return CmdResult("", "", 0)
        if inner[0] == "kill" and not privileged:
            return CmdResult("", "permission denied", 2)
        return table(inner, timeout, interactive)

    set_runner(runner)
    monkeypatch.setattr(daemon_apps, "app_path", lambda _: path)
    monkeypatch.setattr(daemon_apps, "grace_quit", lambda *_: False)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    _, ok, _ = daemon_apps.quit_with_daemons(DaemonApp("Remote", (str(plist),)), {}, save=lambda _: None)
    assert ok
    assert table.rows[871]["stat"] == "gone"
    assert ("sudo", "kill", "-TERM", "871") in calls
    assert not any("pkill" in command for command in calls)


def test_whole_bundle_kill_rejects_resource_ancestor():
    from travel_mode.system.process_control import force_kill

    path = "/Applications/Ancestor.app"
    runner = ProcessRunner({os.getppid(): _row(path + "/Contents/Resources/Parent")})
    set_runner(runner)
    with pytest.raises(PermissionError):
        force_kill(path, whole_bundle=True)
    assert not any(command[0] in {"kill", "pkill"} for command in runner.calls)


def test_new_freeze_preserves_merged_legacy_restore_scope(tmp_path):
    from travel_mode.actions.journal import mutation_scope
    from travel_mode.state import load_state, save_state

    old = "/Applications/Legacy.app"
    new = "/Applications/Modern.app"
    runner = ProcessRunner(
        {
            881: _row(old + "/Contents/MacOS/Legacy", "T"),
            882: _row(new + "/Contents/MacOS/Modern"),
            883: _row(new + "/Contents/Helpers/UnrelatedStop", "T"),
        }
    )
    set_runner(runner)
    state = {"frozen_apps": [old]}
    target = tmp_path / "checkpoint.json"
    with mutation_scope(state, lambda value: save_state(value, target)):
        assert system_apps.freeze_app(new)
    persisted = load_state(target)
    assert flow_restore.run_restore(persisted, lambda *_: None)[1] == 0
    assert runner.rows[881]["stat"] == "S"
    assert runner.rows[882]["stat"] == "S"
    assert runner.rows[883]["stat"] == "T"


def test_thaw_all_selects_stopped_app_processes(monkeypatch, capsys):
    commands = []

    def fake_runner(cmd, timeout=None, interactive=False):
        commands.append(cmd)
        if cmd[:3] == ["ps", "-axo", "pid,stat,comm"]:
            return CmdResult(
                "PID STAT COMMAND\n123 T /Applications/Demo.app/Contents/MacOS/Demo\n"
                "456 S /Applications/Other.app/Contents/MacOS/Other\n"
                "789 T /usr/bin/tool\n",
                "",
                0,
            )
        if cmd[:2] == ["kill", "-CONT"]:
            return CmdResult("", "", 0)
        return CmdResult("", "unexpected", 1)

    from travel_mode.system.shell import set_runner

    set_runner(fake_runner)
    assert flow_thaw.thaw_processes(all_processes=True) == 0
    assert ["kill", "-CONT", "123"] in commands
    assert "123" in capsys.readouterr().out

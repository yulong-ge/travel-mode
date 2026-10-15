"""The guard refuses arbitrary system mutations before invoking a backend."""

import pytest
from e2e_guard import PROCESS_QUERY, STATUS_QUERY, GuardViolation

from conftest import FakeRunner
from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize(
    "cmd",
    [
        ["sudo", "-v"],
        ["sudo", "-n", "true"],
        ["pkill", "-x", "Demo"],
        ["kill", "-STOP", "77"],
        ["kill", "-CONT", "77"],
        ["kill", "-TERM", "77"],
        ["kill", "-KILL", "77"],
        ["kill", "-STOP", "-1"],
        ["kill", "-STOP", "0"],
        ["sudo", "pmset", "-a", "sleep", "2"],
        ["sudo", "pmset", "-c", "sleep", "2"],
        ["sudo", "-n", "/usr/bin/pmset", "-a", "sleep", "2"],
        ["sudo", "-n", "/usr/bin/pmset", "-c", "sleep", "2"],
        ["sudo", "pmset", "-b", "disablesleep", "0"],
        ["sudo", "pmset", "-b", "hibernatemode", "24"],
        ["sudo", "pmset", "-b", "sleep", "1", "-c", "2"],
        ["sudo", "pmset", "-b", "sleep", "$(touch bad)"],
        ["sudo", "pmset", "-b", "sleep"],
        ["sudo", "pmset", "-b", "sleep", "1", "sleep", "2"],
        ["sudo", "pmset", "-b", "sleep", "181"],
        ["pmset", "-b", "sleep", "2"],
        ["networksetup", "-setairportpower", "en0", "off"],
        ["blueutil", "-p", "0"],
        ["mdutil", "-i", "off", "/"],
        ["osascript", "-e", 'tell application id "com.real.app" to quit'],
        ["launchctl", "unload", "/Library/LaunchAgents/real.plist"],
        ["sudo", "launchctl", "load", "/tmp/agent.plist"],
        ["launchctl", "bootstrap", "gui/501", "/tmp/agent.plist"],
        ["open", "/Applications/Real.app"],
        ["open", "-a", "Real"],
        ["/bin/sh", "-c", "anything"],
        ["ps", "--unexpected"],
        ["/usr/bin/pmset", "-g", "unknown"],
        [],
    ],
)
def test_guard_rejects_each_unsafe_command(cmd, sandbox):
    _, guard, machine = sandbox
    with pytest.raises(GuardViolation, match="REFUSED"):
        guard(cmd)
    assert machine.calls == []
    assert guard.tripped


def test_guard_accepts_all_documented_commands(sandbox):
    _, guard, _ = sandbox
    commands = [
        PROCESS_QUERY,
        STATUS_QUERY,
        ["launchctl", "list"],
        ["mdfind", 'kMDItemContentType == "com.apple.application-bundle"'],
        ["ps", "-axo", "pid=,%cpu=,command="],
        ["sudo", "-n", "/usr/bin/pmset", "-g", "ps"],
    ]
    commands += [["pmset", "-g", kind] for kind in ("custom", "cap", "ps")]
    commands += [["launchctl", op, str(guard.plist)] for op in ("load", "unload")]
    commands += [[exe] for exe in guard.executables]
    commands += [["open", str(app)] for app in guard.apps]
    for mode in ("lowpowermode", "powermode"):
        commands += [["sudo", "pmset", "-b", "displaysleep", "3", "sleep", "5", "powernap", "0", mode, "1"]]
    guard.owned[900001] = ("birth", guard.shells[0])
    commands += [["kill", sig, "900001"] for sig in ("-STOP", "-CONT", "-TERM", "-KILL")]
    # Plan mode validates commands, but the strict backend must remain untouched.
    backend = FakeRunner()
    guard.backend = backend
    guard.plan = True
    for cmd in commands:
        assert guard(cmd).rc == 0
    assert backend.calls == []


def test_guard_rejects_reused_pid_and_propagates_identity_query_failure(sandbox):
    _, guard, machine = sandbox
    guard.owned[77] = ("old birth", guard.shells[0])
    with pytest.raises(GuardViolation):
        guard(["kill", "-STOP", "77"])
    assert not any(cmd[0] == "kill" for cmd in machine.calls)
    guard.backend = FakeRunner({tuple(PROCESS_QUERY): CmdResult("", "ps failed", 1)})
    with pytest.raises(RuntimeError, match="ps failed"):
        guard(["kill", "-STOP", "77"])


def test_guard_rejects_interactive_execution(sandbox):
    _, guard, machine = sandbox
    with pytest.raises(GuardViolation):
        guard(["launchctl", "list"], interactive=True)
    assert not machine.calls


@pytest.mark.parametrize(
    "cmd",
    [
        ["sudo", "-n", "-l", "/usr/bin/pmset", "-c", "sleep", "0"],
        ["sudo", "-n", "/usr/bin/pmset", "-c", "powernap", "0", "womp", "0", "sleep", "0"],
        ["sudo", "-n", "/usr/bin/pmset", "-a", "disablesleep", "2"],
        ["sudo", "-n", "/usr/bin/pmset", "-b", "hibernatemode", "24"],
        ["osacompile", "-s", "-o", "/Applications/Other.app", "-e", "on idle"],
        ["codesign", "--force", "--sign", "-", "/Applications/Other.app"],
        ["open", "-g", "-j", "/Applications/Other.app"],
        [
            "osascript",
            "-e",
            "with timeout of 8 seconds",
            "-e",
            'tell application id "com.real.app" to quit',
            "-e",
            "end timeout",
        ],
    ],
)
def test_extended_guard_rejects_escape_and_extra_keys(cmd, sandbox):
    _, guard, machine = sandbox
    with pytest.raises(GuardViolation):
        guard(cmd)
    assert not machine.calls


def test_extended_guard_accepts_only_exact_safe_commands(sandbox):
    _, guard, machine = sandbox
    from e2e_guard import LSREGISTER, QUIT_EVENT

    commands = [
        QUIT_EVENT,
        ["open", "-g", "-j", str(guard.graceful)],
        [LSREGISTER, "-u", str(guard.graceful)],
    ]
    for argv in [
        ["-a", "disablesleep", "0"],
        ["-a", "disablesleep", "1"],
        ["-c", "powernap", "0", "womp", "0"],
        ["-c", "powernap", "1", "womp", "1"],
        ["-b", "hibernatemode", "25"],
        ["-b", "hibernatemode", "3"],
    ]:
        commands += [["sudo", "-n", "/usr/bin/pmset", *argv], ["sudo", "-n", "-l", "/usr/bin/pmset", *argv]]
    guard.plan = True
    for cmd in commands:
        guard(cmd)
    assert not machine.calls


def test_readonly_timeout_reaches_backend_and_keeps_diagnostic_trace(sandbox, monkeypatch):
    import e2e_guard
    from e2e_guard import LocalBackend

    _, guard, _ = sandbox
    calls = []

    def backend(cmd, timeout=None, interactive=False):
        calls.append((cmd, timeout, interactive))
        return CmdResult("", "timeout", 124)

    monkeypatch.setattr(e2e_guard, "subprocess_runner", backend)
    guard.backend = LocalBackend([])
    result = guard(["ioreg", "-rn", "AppleSmartBattery"], timeout=10)
    assert result.rc == 124 and calls == [(["ioreg", "-rn", "AppleSmartBattery"], 10, False)]
    assert not guard.tripped and not guard.violations
    trace = guard.trace[-1]
    assert trace["command"] == calls[0][0] and trace["timeout"] == 10
    assert trace["rc"] == 124 and trace["seconds"] >= 0
    assert trace["thread"] and trace["stdout_bytes"] == 0


@pytest.mark.parametrize(
    ("logical", "executed"),
    [
        (["ioreg", "-rn", "AppleSmartBattery"], ["/usr/sbin/ioreg", "-rn", "AppleSmartBattery"]),
        (["pmset", "-g", "custom"], ["/usr/bin/pmset", "-g", "custom"]),
        (
            ["sudo", "-n", "-l", "pmset", "-g", "ps"],
            ["/usr/bin/sudo", "-n", "-l", "/usr/bin/pmset", "-g", "ps"],
        ),
    ],
)
def test_local_backend_pins_paths_after_exact_guard_validation(sandbox, monkeypatch, logical, executed):
    import subprocess

    from e2e_guard import LocalBackend

    from travel_mode.system import shell

    _, guard, _ = sandbox
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "fixture", "")

    monkeypatch.setattr(shell, "_is_executable", lambda _: True)
    monkeypatch.setattr(shell.subprocess, "run", run)
    monkeypatch.setattr(shell.shutil, "which", lambda _: pytest.fail("guard/system tool used PATH"))
    guard.backend = LocalBackend([])
    assert guard(logical, timeout=10).rc == 0
    assert calls == [executed]
    assert guard.trace[-1]["executable"] == executed[0]
    assert not guard.tripped

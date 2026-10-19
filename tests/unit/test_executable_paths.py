"""Logical Runner commands resolve only at the real subprocess boundary."""

import shutil
import subprocess

import pytest

from travel_mode.system import shell

PATH_WHICH = shutil.which


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["ioreg", "-rn", "AppleSmartBattery"], ["/usr/sbin/ioreg", "-rn", "AppleSmartBattery"]),
        (["sw_vers", "-productVersion"], ["/usr/bin/sw_vers", "-productVersion"]),
        (["sudo", "pmset", "-g"], ["/usr/bin/sudo", "-n", "/usr/bin/pmset", "-g"]),
        (["sudo", "-n", "-l", "pmset", "-g"], ["/usr/bin/sudo", "-n", "-l", "/usr/bin/pmset", "-g"]),
    ],
)
def test_real_boundary_pins_system_paths(command, expected, monkeypatch, tmp_path):
    for name in ("ioreg", "sw_vers", "sudo", "pmset"):
        (tmp_path / name).write_text("#!/bin/sh\nexit 99\n")
        (tmp_path / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(shell, "_is_executable", lambda _: True, raising=False)
    observed = []

    def run(cmd, **kwargs):
        observed.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "fixture", "")

    monkeypatch.setattr(shell.subprocess, "run", run)
    result = shell.subprocess_runner(command, timeout=3)
    assert result == shell.CmdResult("fixture", "", 0)
    assert observed == [
        (expected, dict(capture_output=True, text=True, errors="replace", timeout=3, check=False))
    ]


def test_missing_system_path_never_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(shell, "_is_executable", lambda _: False)
    monkeypatch.setattr(shell.shutil, "which", lambda _: pytest.fail("system tool used PATH"))
    monkeypatch.setattr(shell.subprocess, "run", lambda *a, **k: pytest.fail("missing tool executed"))
    from travel_mode.i18n import set_language

    set_language("en")
    with pytest.raises(shell.CommandResolutionError, match="/usr/sbin/ioreg"):
        shell.resolve_command(["ioreg", "-rn", "AppleSmartBattery"])
    result = shell.subprocess_runner(["ioreg", "-rn", "AppleSmartBattery"])
    assert result.rc == 127 and "/usr/sbin/ioreg" in result.stderr
    assert "PATH fallback is disabled" in result.stderr
    shell.set_runner(shell.subprocess_runner)
    with pytest.raises(shell.ReadError, match="/usr/sbin/ioreg"):
        shell._read(["ioreg", "-rn", "AppleSmartBattery"])


@pytest.mark.parametrize("tool", shell.THIRD_PARTY_TOOLS)
def test_third_party_tool_uses_path_and_is_reported(tool, monkeypatch, tmp_path):
    executable = tmp_path / tool
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(shell.shutil, "which", PATH_WHICH)
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(shell.subprocess, "run", run)
    assert shell.subprocess_runner([tool, "-l"]).rc == 0
    assert calls == [[str(executable), "-l"]]
    assert shell.optional_tool_paths()[tool] == str(executable)
    executable.unlink()
    assert shell.optional_tool_paths()[tool] is None
    assert shell.subprocess_runner([tool, "-l"]).rc == 127
    assert len(calls) == 1


@pytest.mark.parametrize("tool", shell.SYSTEM_TOOLS)
def test_each_apple_tool_ignores_path_and_keeps_logical_argv(tool, monkeypatch):
    monkeypatch.setattr(shell, "_is_executable", lambda _: True)
    monkeypatch.setattr(shell.shutil, "which", lambda _: pytest.fail("Apple tool used PATH"))
    logical = [tool] if tool != "sudo" else [tool, "-n", "true"]
    original = logical.copy()
    resolved = shell.resolve_command(logical)
    assert resolved[0] == shell.SYSTEM_TOOLS[tool]
    assert logical == original
    assert shell.resolve_command(resolved) == resolved


def test_sudo_authentication_probe_and_execution_use_same_target(monkeypatch):
    from travel_mode.system.sudo import credential_scope

    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "NOPASSWD: ALL", "")

    monkeypatch.setattr(shell, "_is_executable", lambda _: True)
    monkeypatch.setattr(shell.subprocess, "run", run)
    shell.set_runner(shell.subprocess_runner)
    # No host mutation: both the credential probe and target use the mocked subprocess.
    with credential_scope():
        shell.run_rc(["pmset", "-b", "lowpowermode", "1"], sudo=True)
    assert calls == [
        ["/usr/bin/sudo", "-n", "-l", "/usr/bin/pmset", "-b", "lowpowermode", "1"],
        ["/usr/bin/sudo", "-n", "/usr/bin/pmset", "-b", "lowpowermode", "1"],
    ]


def test_interactive_sudo_and_invalid_arguments(monkeypatch):
    monkeypatch.setattr(shell, "_is_executable", lambda _: True)
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(shell.subprocess, "run", run)
    assert shell.subprocess_runner(["sudo", "-v"], timeout=4, interactive=True).rc == 0
    assert calls == [(["/usr/bin/sudo", "-v"], {"timeout": 4, "check": False})]
    with pytest.raises(ValueError):
        shell.resolve_command([])
    with pytest.raises(ValueError):
        shell.resolve_command(["sudo", "-u", "root", "pmset", "-g"])


def test_timeout_keeps_existing_result_semantics(monkeypatch):
    monkeypatch.setattr(shell, "_is_executable", lambda _: True)

    def run(cmd, **kwargs):
        assert cmd == ["/usr/sbin/ioreg", "-rn", "AppleSmartBattery"]
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(shell.subprocess, "run", run)
    assert shell.subprocess_runner(["ioreg", "-rn", "AppleSmartBattery"], timeout=1).rc == 124


def test_optional_paths_are_in_json_and_text_check(monkeypatch, capsys):
    from travel_mode import structured
    from travel_mode.flows import check
    from travel_mode.i18n import set_language

    set_language("en")
    monkeypatch.setattr(shell.shutil, "which", lambda tool: f"/fixture/bin/{tool}")
    result = structured.check_data()
    assert result["tool_paths"] == {tool: f"/fixture/bin/{tool}" for tool in shell.THIRD_PARTY_TOOLS}
    check.mode_check(verbose=True)
    output = capsys.readouterr().out
    assert "Optional tool paths" in output
    for path in result["tool_paths"].values():
        assert path in output


def test_system_availability_checks_ignore_path(monkeypatch):
    from travel_mode.system import platform

    monkeypatch.setattr(shell.shutil, "which", lambda _: pytest.fail("Apple availability used PATH"))
    monkeypatch.setattr(shell, "_is_executable", lambda path: path != "/usr/bin/pmset")
    assert platform.missing_required_commands() == ["pmset"]
    # Explicit aliases cannot select a PATH-provided executable either.
    assert shell.resolve_command(["/untrusted/bin/ioreg", "-rn", "AppleSmartBattery"])[0] == "/usr/sbin/ioreg"


def test_missing_sudo_target_stops_before_sudo_execution(monkeypatch):
    monkeypatch.setattr(shell, "_is_executable", lambda path: path != "/usr/bin/pmset")
    monkeypatch.setattr(
        shell.subprocess, "run", lambda *a, **k: pytest.fail("sudo executed with missing target")
    )
    result = shell.subprocess_runner(["sudo", "-n", "-l", "pmset", "-g"])
    assert result.rc == 127 and "/usr/bin/pmset" in result.stderr


def test_fake_and_dry_run_keep_logical_commands(fake_runner, monkeypatch, capsys):
    monkeypatch.setattr(shell, "resolve_command", lambda _: pytest.fail("logical Runner resolved argv"))
    shell.run_rc(["ioreg", "-rn", "AppleSmartBattery"])
    assert fake_runner.calls[-1] == ("ioreg", "-rn", "AppleSmartBattery")
    shell.DryRunRunner(fake_runner)(["sudo", "pmset", "-b", "lowpowermode", "1"])
    assert "[dry-run] sudo pmset -b lowpowermode 1" in capsys.readouterr().out


def test_sudo_empty_prompt_keeps_system_target_pinned(monkeypatch):
    monkeypatch.setattr(shell, "_is_executable", lambda _: True)
    assert shell.resolve_command(["sudo", "-v", "-p", ""]) == ["/usr/bin/sudo", "-v", "-p", ""]
    assert shell.resolve_command(["sudo", "-p", "", "pmset", "-g"]) == [
        "/usr/bin/sudo",
        "-p",
        "",
        "/usr/bin/pmset",
        "-g",
    ]
    with pytest.raises(ValueError, match="-p"):
        shell.resolve_command(["sudo", "-v", "-p"])

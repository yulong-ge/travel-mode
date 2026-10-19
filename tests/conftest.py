"""All tests isolate user paths; ordinary tests never reach the host runner."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from travel_mode import i18n
from travel_mode.system import apps, brightness, daemon_discovery, launchd, platform, shell
from travel_mode.system.shell import CmdResult, set_runner, subprocess_runner
from user_data_snapshot import assert_unchanged, snapshot_user_data

REAL_HOME = Path.home()


class FakeRunner:
    """Strict command double keyed by command prefixes."""

    def __init__(self, responses: dict[tuple[str, ...], CmdResult] | None = None, *, strict=True):
        self.responses = responses or {}
        self.strict = strict
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, cmd, timeout=None, interactive=False):
        key = tuple(cmd)
        self.calls.append(key)
        for prefix, result in sorted(self.responses.items(), key=lambda item: len(item[0]), reverse=True):
            if key[: len(prefix)] == prefix:
                return result
        if self.strict:
            pytest.fail(f"unexpected command: {' '.join(cmd)}")
        return CmdResult("", "", 0)


def _readonly_responses() -> dict[tuple[str, ...], CmdResult]:
    fixture = Path(__file__).parent / "fixtures"
    return {
        ("ps", "-axo", "pid=,ppid=,uid=,stat=,%cpu=,lstart=,comm="): CmdResult(
            "900000 1 0 S 0.0 Thu Oct 15 12:00:00 2026 /System/Library/CoreServices/loginwindow", "", 0
        ),
        (sys.executable, "-m", "travel_mode.system.health_native", "displays"): CmdResult("[]", "", 0),
        (sys.executable, "-m", "travel_mode.system.health_native", "brightness"): CmdResult("0.5", "", 0),
        ("tmutil", "status"): CmdResult("Backup session status:\n{ Running = 0; }", "", 0),
        ("lsappinfo", "front"): CmdResult("ASN:0x0-0x1:", "", 0),
        ("lsappinfo", "info"): CmdResult('bundleID="org.demo.front"\npid = 900000', "", 0),
        ("ioreg", "-rn", "AppleSmartBattery"): CmdResult(
            '"ExternalConnected" = Yes\n"IsCharging" = No\n"Voltage" = 12000\n'
            '"AppleRawCurrentCapacity" = 4000\n',
            "",
            0,
        ),
        ("pmset", "-g", "custom"): CmdResult((fixture / "pmset-g-custom.txt").read_text(), "", 0),
        ("pmset", "-g"): CmdResult((fixture / "pmset-g.txt").read_text(), "", 0),
        ("pmset", "-g", "batt"): CmdResult(
            "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t87%;", "", 0
        ),
        ("pmset", "-g", "ps"): CmdResult("Currently drawing from 'Battery Power'\n", "", 0),
        ("pmset", "-g", "assertions"): CmdResult("Assertion status system-wide:\n", "", 0),
        ("pmset", "-g", "cap"): CmdResult("sleep displaysleep hibernatemode powermode\n", "", 0),
        ("launchctl", "list"): CmdResult("PID\tStatus\tLabel\n", "", 0),
        ("mdutil", "-s"): CmdResult("/:\tIndexing enabled.\n", "", 0),
        ("networksetup", "-listallhardwareports"): CmdResult("Hardware Port: Wi-Fi\nDevice: en0\n", "", 0),
        ("networksetup", "-getairportpower"): CmdResult("Wi-Fi Power (en0): On\n", "", 0),
        ("blueutil", "-p"): CmdResult("1\n", "", 0),
        ("brightness", "-l"): CmdResult("brightness 0.8\n", "", 0),
        ("ps",): CmdResult("", "", 0),
        ("pgrep",): CmdResult("", "", 1),
        ("top",): CmdResult("", "", 0),
        ("mdfind",): CmdResult("", "", 0),
        ("sudo", "-n", "-l"): CmdResult("NOPASSWD: ALL", "", 0),
        ("sudo", "-n", "true"): CmdResult("", "", 0),
    }


class ReadOnlyHostRunner:
    """Reject host mutations before subprocess execution, including sudo."""

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, cmd, timeout=None, interactive=False):
        key = tuple(cmd)
        allowed = (
            key[:2] == ("pmset", "-g")
            or key == ("launchctl", "list")
            or key == ("ioreg", "-rn", "AppleSmartBattery")
            or key[:2] == ("mdutil", "-s")
            or key[:2] == ("brightness", "-l")
            or (key and key[0] in {"ps", "pgrep", "top", "mdfind"})
            or (len(key) > 1 and key[0] == "networksetup" and key[1].startswith(("-list", "-get")))
        )
        assert allowed and not interactive, f"Forbidden host command in macos test: {key!r}"
        self.calls.append(key)
        return subprocess_runner(cmd, timeout, interactive)


@pytest.fixture
def fake_runner():
    return FakeRunner(_readonly_responses())


@pytest.fixture(scope="session", autouse=True)
def real_user_data_session_guard():
    before = snapshot_user_data(REAL_HOME)
    try:
        yield
    finally:
        assert_unchanged(before, snapshot_user_data(REAL_HOME))


@pytest.fixture(autouse=True)
def real_user_data_guard(real_user_data_session_guard):
    before = snapshot_user_data(REAL_HOME)
    try:
        yield
    finally:
        assert_unchanged(before, snapshot_user_data(REAL_HOME))


@pytest.fixture(autouse=True)
def hermetic_environment(request, monkeypatch):
    """Pin ambient inputs; tests of a probe must explicitly override its double."""
    if request.node.get_closest_marker("macos") is not None:
        yield
        return
    from types import SimpleNamespace

    monkeypatch.setattr(apps, "time", SimpleNamespace(sleep=lambda _: None))
    previous_tz = os.environ.get("TZ")
    for name in tuple(os.environ):
        if (
            name.startswith(("LC_", "TRAVEL_MODE_", "SUDO_")) and name != "TRAVEL_MODE_RECORD_INITIAL"
        ) or name in {
            "FORCE_COLOR",
            "CLICOLOR",
            "CLICOLOR_FORCE",
            "COLORTERM",
            "EDITOR",
            "VISUAL",
        }:
            monkeypatch.delenv(name)
    for name, value in {
        "LANG": "en_US.UTF-8",
        "TZ": "UTC",
        "USER": "fixture",
        "LOGNAME": "fixture",
        "COLUMNS": "120",
        "LINES": "40",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "CI": "true",
    }.items():
        monkeypatch.setenv(name, value)
    time.tzset()
    monkeypatch.setattr(i18n, "_language", "zh")
    monkeypatch.setattr(brightness, "available", lambda: False)
    commands = frozenset(platform.REQUIRED_COMMANDS)
    monkeypatch.setattr(shell, "_is_executable", lambda path: path in shell.SYSTEM_TOOLS.values())
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, mode=os.F_OK | os.X_OK, path=None: f"/fixture/bin/{name}" if name in commands else None,
    )
    monkeypatch.setattr(os, "isatty", lambda fd: False)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        monkeypatch.setattr(stream, "isatty", lambda: False)
    monkeypatch.setattr(os, "get_terminal_size", lambda fd=1: os.terminal_size((120, 40)))

    popen = subprocess.Popen

    def forbidden_process(command, *args, **kwargs):
        # Preserve the explicit initial-recorder's read-only tracking check.
        # This is repository metadata, never a product/system observation.
        if (
            os.environ.get("TRAVEL_MODE_RECORD_INITIAL") == "1"
            and isinstance(command, list)
            and len(command) == 4
            and command[:3] == ["git", "ls-files", "--error-unmatch"]
            and Path(command[3]).parent == Path(__file__).parent / "characterization/golden"
        ):
            return popen(command, *args, **kwargs)
        pytest.fail(f"Host subprocess forbidden in hermetic test: {command!r}")

    monkeypatch.setattr(subprocess, "Popen", forbidden_process)
    try:
        yield
    finally:
        # Restore libc's process-local timezone as well as the environment.
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()


@pytest.fixture
def darwin_platform(monkeypatch):
    """Model the product's platform check without changing third-party sys.platform."""

    class DarwinSystem:
        platform = "darwin"

        def __getattr__(self, name):
            return getattr(sys, name)

    monkeypatch.setattr(platform, "sys", DarwinSystem())


@pytest.fixture(autouse=True)
def isolated_user_paths(real_user_data_guard, hermetic_environment, tmp_path, monkeypatch):
    # macos only selects the command runner; it must never bypass path isolation.
    monkeypatch.setenv("TRAVEL_MODE_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("TRAVEL_MODE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for variable, directory in {
        "XDG_CONFIG_HOME": "xdg-config",
        "XDG_STATE_HOME": "xdg-state",
        "XDG_DATA_HOME": "xdg-data",
        "XDG_CACHE_HOME": "xdg-cache",
        "XDG_RUNTIME_DIR": "xdg-runtime",
        "XDG_CONFIG_DIRS": "xdg-config-dirs",
        "XDG_DATA_DIRS": "xdg-data-dirs",
    }.items():
        monkeypatch.setenv(variable, str(tmp_path / directory))
    monkeypatch.setattr(launchd, "agent_directories", lambda: (Path.home() / "Library/LaunchAgents",))
    monkeypatch.setattr(apps, "APP_DIRS", [str(tmp_path / "Applications")])
    monkeypatch.setattr(daemon_discovery, "app_directories", lambda: (Path.home() / "Applications",))
    monkeypatch.setattr(
        daemon_discovery, "plist_directories", lambda: (Path.home() / "Library/LaunchAgents",)
    )
    return tmp_path


@pytest.fixture
def tmp_paths(isolated_user_paths):
    return isolated_user_paths


@pytest.fixture(autouse=True)
def strict_fake_runner(request, fake_runner, isolated_user_paths):
    runner = ReadOnlyHostRunner() if request.node.get_closest_marker("macos") is not None else fake_runner
    set_runner(runner)
    try:
        yield runner
    finally:
        set_runner(subprocess_runner)


__all__ = ["FakeRunner"]

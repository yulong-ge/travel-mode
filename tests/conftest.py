"""All tests isolate user paths; ordinary tests never reach the host runner."""

from __future__ import annotations

from pathlib import Path

import pytest

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
        ("sudo", "-n", "-l"): CmdResult("", "", 0),
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
def isolated_user_paths(real_user_data_guard, tmp_path, monkeypatch):
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

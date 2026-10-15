"""Regression coverage for default isolation and the read-only data guard."""

import os
from pathlib import Path

import pytest

from conftest import ReadOnlyHostRunner
from travel_mode.paths import config_path, legacy_conf_path, legacy_state_path, state_path
from user_data_snapshot import assert_unchanged, snapshot_tree


def test_paths_are_isolated_without_requesting_tmp_paths(tmp_path):
    assert Path.home() == tmp_path
    assert config_path() == tmp_path / "config.toml"
    assert state_path() == tmp_path / "state.json"
    assert legacy_conf_path().is_relative_to(tmp_path)
    assert legacy_state_path().is_relative_to(tmp_path)
    for variable in (
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
        "XDG_CONFIG_DIRS",
        "XDG_DATA_DIRS",
    ):
        assert Path(os.environ[variable]).is_relative_to(tmp_path), variable


def test_deleting_overrides_still_uses_isolated_xdg_and_home(tmp_path, monkeypatch):
    monkeypatch.delenv("TRAVEL_MODE_STATE")
    monkeypatch.delenv("TRAVEL_MODE_CONFIG")
    assert state_path().is_relative_to(tmp_path)
    assert config_path().is_relative_to(tmp_path)
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert state_path() == tmp_path / ".local/state/travel-mode/state.json"
    assert config_path() == tmp_path / ".config/travel-mode/config.toml"


def test_snapshot_does_not_create_missing_directory(tmp_path):
    missing = tmp_path / "missing"
    assert snapshot_tree(missing) == {"exists": False, "entries": {}}
    assert not missing.exists()


@pytest.mark.parametrize("change", ["content", "mtime", "added", "deleted"])
def test_guard_detects_user_data_changes_in_sandbox(tmp_path, change):
    target = tmp_path / "state.json"
    target.write_text("original", encoding="utf-8")
    before = snapshot_tree(tmp_path)
    original_mtime = target.stat().st_mtime_ns
    if change == "content":
        target.write_text("modified", encoding="utf-8")
        os.utime(target, ns=(target.stat().st_atime_ns, original_mtime))
    elif change == "mtime":
        os.utime(target, ns=(target.stat().st_atime_ns, original_mtime + 1_000_000))
    elif change == "added":
        (tmp_path / "new.json").write_text("new", encoding="utf-8")
    else:
        target.unlink()
    with pytest.raises(AssertionError, match="user data changed"):
        assert_unchanged(before, snapshot_tree(tmp_path))


@pytest.mark.parametrize(
    "command",
    [
        ["sudo", "-n", "true"],
        ["pmset", "-b", "sleep", "5"],
        ["launchctl", "unload", "demo.plist"],
        ["mdutil", "-i", "off", "/"],
        ["networksetup", "-setairportpower", "en0", "off"],
        ["blueutil", "-p"],
        ["pkill", "-STOP", "-f", "Demo.app"],
        ["brightness", "0.15"],
    ],
)
def test_macos_runner_rejects_forbidden_commands_before_execution(command, monkeypatch):
    def forbidden_subprocess(*args, **kwargs):
        pytest.fail("Rejected command reached subprocess_runner")

    monkeypatch.setattr("conftest.subprocess_runner", forbidden_subprocess)
    with pytest.raises(AssertionError, match="Forbidden host command"):
        ReadOnlyHostRunner()(command)

"""Configured daemon app shutdown workflow tests."""

from __future__ import annotations

import plistlib

import pytest

from travel_mode.config import DaemonApp
from travel_mode.system import daemon_apps
from travel_mode.system.shell import CmdResult, set_runner


def _state():
    return {"unloaded_sys_svcs": [], "unloaded_svcs": [], "quit_apps": []}


def test_quit_with_daemons_runs_unload_then_quit_and_records_state(tmp_path, monkeypatch):
    sys_plist = tmp_path / "system.plist"
    user_plist = tmp_path / "user.plist"
    _plist(sys_plist, "com.example.system")
    _plist(user_plist, "com.example.user")
    app = tmp_path / "Remote.app"
    app.mkdir()
    spec = DaemonApp("Remote", (str(sys_plist),), (str(user_plist),))
    state = _state()
    commands = []
    saved = []

    def fake_run_rc(command, timeout=None, sudo=False, interactive=False):
        if command == ["launchctl", "list"]:
            return "- 0 com.example.system\n- 0 com.example.user\n", 0
        commands.append((command, sudo))
        return "", 0

    monkeypatch.setattr(daemon_apps, "run_rc", fake_run_rc)
    monkeypatch.setattr(daemon_apps, "app_path", lambda name: str(app))
    monkeypatch.setattr(daemon_apps, "grace_quit", lambda path, name: True)
    alive = iter([True, False, False, False])
    monkeypatch.setattr(daemon_apps, "bundle_alive", lambda path: next(alive))

    path, ok, message = daemon_apps.quit_with_daemons(spec, state, save=saved.append)

    assert (path, ok) == (str(app), True)
    assert commands == [
        (["launchctl", "unload", str(sys_plist)], True),
        (["launchctl", "unload", str(user_plist)], False),
    ]
    assert state["unloaded_sys_svcs"] == [str(sys_plist)]
    assert state["unloaded_svcs"] == [str(user_plist)]
    assert state["quit_apps"] == [str(app)]
    assert saved and saved[-1] == state
    assert "Remote" in message


def test_no_new_records_does_not_save(tmp_path, monkeypatch):
    spec = DaemonApp("Missing", (), ())
    state = _state()
    saved = []
    monkeypatch.setattr(daemon_apps, "app_path", lambda name: "")

    path, ok, _ = daemon_apps.quit_with_daemons(spec, state, save=saved.append)

    assert (path, ok) == ("", True)
    assert saved == []


def test_existing_records_are_not_saved_again(tmp_path, monkeypatch):
    sys_plist = tmp_path / "system.plist"
    _plist(sys_plist, "com.example.system")
    app = tmp_path / "Remote.app"
    app.mkdir()
    spec = DaemonApp("Remote", (str(sys_plist),), ())
    state = _state()
    state["unloaded_sys_svcs"].append(str(sys_plist))
    state["quit_apps"].append(str(app))
    saved = []

    monkeypatch.setattr(daemon_apps, "run_rc", lambda *args, **kwargs: ("", 0))
    monkeypatch.setattr(daemon_apps, "app_path", lambda name: str(app))
    monkeypatch.setattr(daemon_apps, "grace_quit", lambda path, name: True)
    monkeypatch.setattr(daemon_apps, "bundle_alive", lambda path: False)

    daemon_apps.quit_with_daemons(spec, state, save=saved.append)

    assert saved == []


def test_residual_process_is_failure_but_unload_records_are_persisted(tmp_path, monkeypatch):
    sys_plist = tmp_path / "system.plist"
    _plist(sys_plist, "com.example.system")
    app = tmp_path / "Remote.app"
    app.mkdir()
    spec = DaemonApp("Remote", (str(sys_plist),), ())
    state = _state()
    commands = []
    saved = []

    def fake_run_rc(command, timeout=None, sudo=False, interactive=False):
        if command == ["launchctl", "list"]:
            return "- 0 com.example.system\n", 0
        commands.append((command, sudo))
        return "", 0

    monkeypatch.setattr(daemon_apps, "run_rc", fake_run_rc)
    monkeypatch.setattr(daemon_apps, "app_path", lambda name: str(app))
    monkeypatch.setattr(daemon_apps, "grace_quit", lambda path, name: False)
    monkeypatch.setattr(daemon_apps, "force_kill", lambda path: False)
    monkeypatch.setattr(daemon_apps, "bundle_alive", lambda path: True)
    monkeypatch.setattr(daemon_apps, "stop_bundle", lambda *args, **kwargs: False)

    path, ok, message = daemon_apps.quit_with_daemons(spec, state, save=saved.append)

    assert path == str(app)
    assert ok is False
    assert "残留" in message
    assert commands == [
        (["launchctl", "unload", str(sys_plist)], True),
    ]
    assert state["unloaded_sys_svcs"] == [str(sys_plist)]
    assert saved and saved[-1] == state


def _plist(path, label):
    path.write_bytes(plistlib.dumps({"Label": label}))
    return str(path)


def test_daemon_unload_checkpoint_precedes_interruption(tmp_path, monkeypatch):
    first = _plist(tmp_path / "first.plist", "com.example.first")
    second = _plist(tmp_path / "second.plist", "com.example.second")
    state = _state()
    saved = []

    def fake(cmd, timeout=None, interactive=False):
        if cmd[-1] == second:
            raise KeyboardInterrupt("simulated interruption")
        return CmdResult("- 0 com.example.first\n- 0 com.example.second\n", "", 0)

    set_runner(fake)
    monkeypatch.setattr(daemon_apps, "app_path", lambda _: "")
    with pytest.raises(KeyboardInterrupt):
        daemon_apps.quit_with_daemons(
            DaemonApp("Remote", (first, second)),
            state,
            save=lambda value: saved.append({k: list(v) for k, v in value.items()}),
        )
    assert any(first in checkpoint["unloaded_sys_svcs"] for checkpoint in saved)


def test_daemon_rejects_apple_label_before_unload(tmp_path):
    path = _plist(tmp_path / "disguised.plist", "com.apple.protected")
    calls = []
    set_runner(lambda cmd, *args: calls.append(cmd) or CmdResult("", "", 0))
    with pytest.raises(PermissionError):
        daemon_apps.quit_with_daemons(DaemonApp("Remote", (path,)), _state(), save=lambda _: None)
    assert not any("unload" in cmd for cmd in calls)


def test_daemon_missing_configured_plist_fails_explicitly(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_apps, "app_path", lambda _: "")
    with pytest.raises(FileNotFoundError):
        daemon_apps.quit_with_daemons(
            DaemonApp("Remote", (str(tmp_path / "missing.plist"),)), _state(), save=lambda _: None
        )


def test_daemon_unload_failure_is_not_success(tmp_path, monkeypatch):
    path = _plist(tmp_path / "remote.plist", "com.example.remote")
    set_runner(
        lambda cmd, *args: (
            CmdResult("- 0 com.example.remote\n", "", 0)
            if cmd[-1] == "list"
            else CmdResult("", "permission denied", 2)
        )
    )
    monkeypatch.setattr(daemon_apps, "app_path", lambda _: "")
    _, ok, _ = daemon_apps.quit_with_daemons(DaemonApp("Remote", (path,)), _state(), save=lambda _: None)
    assert not ok


def test_daemon_not_loaded_is_not_unloaded_or_recorded(tmp_path, monkeypatch):
    plist = tmp_path / "idle.plist"
    _plist(plist, "com.example.idle")
    calls, saved = [], []
    state = _state()
    monkeypatch.setattr(daemon_apps, "app_path", lambda _: "")

    def fake(command, **kwargs):
        calls.append(command)
        return "PID Status Label\n", 0

    monkeypatch.setattr(daemon_apps, "run_rc", fake)
    _, ok, _ = daemon_apps.quit_with_daemons(DaemonApp("Idle", (str(plist),)), state, saved.append)
    assert ok
    assert not any(command[:2] == ["launchctl", "unload"] for command in calls)
    assert state["unloaded_sys_svcs"] == []
    assert saved == []

"""Finite post-operation notifications cannot affect recovery or action counts."""

import asyncio
import plistlib
from pathlib import Path

import pytest

from travel_mode import cli, i18n, menubar, state, status
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.config import load_config, write_menubar
from travel_mode.paths import config_path
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


@pytest.fixture
def installed(tmp_path, fake_runner, swiftbar_process):
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / menubar.FILENAME).write_bytes(menubar.payload())
    fake_runner.responses[tuple(menubar.PREFERENCE)] = CmdResult(str(folder), "", 0)
    fake_runner.responses[tuple(menubar.REFRESH)] = CmdResult("", "", 0)
    return folder


@pytest.mark.parametrize("show", ["always", "session"])
def test_cli_on_off_notify_once_after_committed_state(installed, fake_runner, darwin_platform, capsys, show):
    config_path().write_text(f'[menubar]\nshow = "{show}"\n')
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    observations = []
    visible = []

    def runner(command, timeout=None, interactive=False):
        if command == menubar.REFRESH:
            observations.append(state.load_state() is not None)
            visible.append(bool(status.data()["swiftbar"]))
        return fake_runner(command, timeout, interactive)

    shell.set_runner(runner)
    assert cli.main(["on", "--scenario", "work", "--yes", "--no-measure"]) == 0
    counts = state.load_state()["session"]["actions"]
    assert counts["attempted"] == counts["succeeded"] and counts["failed"] == 0
    assert cli.main(["off"]) == 0
    assert observations == [True, False]
    assert visible == [True, show == "always"]
    assert fake_runner.calls.count(tuple(menubar.REFRESH)) == 2
    assert "failed" not in capsys.readouterr().err


@pytest.mark.parametrize("rc", [0, 1])
async def test_tui_transaction_notifies_after_commit_without_counting_refresh(installed, fake_runner, rc):
    fake_runner.responses[tuple(menubar.REFRESH)] = CmdResult("", "SwiftBar unavailable" if rc else "", rc)
    snapshot = state.new_state()
    executor = Executor(Context(snapshot, state.save_state))
    async with executor.transaction(asyncio.Lock(), snapshot, False, state.save_state):
        snapshot["scenario"] = "work"
        state.save_state(snapshot)
    assert fake_runner.calls.count(tuple(menubar.REFRESH)) == 1
    assert not snapshot.get("session") and not snapshot.get("action_failures")
    if rc:
        log = (state._path().parent / "travel-mode.log").read_text()
        assert '"kind": "observation"' in log and "SwiftBar unavailable" in log


def test_refresh_failure_preserves_primary_exception_and_saved_recovery(installed, fake_runner):
    fake_runner.responses[tuple(menubar.REFRESH)] = CmdResult("", "no handler", 1)
    with pytest.raises(RuntimeError, match="primary failure"), menubar.status_changes():
        state.save_state(state.new_state())
        raise RuntimeError("primary failure")
    assert state.load_state() is not None
    assert fake_runner.calls.count(tuple(menubar.REFRESH)) == 1


def test_uninstalled_plugin_never_reads_missing_file_or_opens_url(tmp_path, fake_runner, monkeypatch):
    folder = tmp_path / "plugins"
    folder.mkdir()
    fake_runner.responses[tuple(menubar.PREFERENCE)] = CmdResult(str(folder), "", 0)
    original = Path.read_bytes

    def read(path):
        if path == folder / menubar.FILENAME:
            pytest.fail("uninstalled plugin was read")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with menubar.status_changes():
        state.save_state(state.new_state())
    assert fake_runner.calls == [tuple(menubar.PREFERENCE)]


def test_no_swiftbar_is_an_observation_only(fake_runner):
    with menubar.status_changes():
        state.save_state(state.new_state())
    assert state.load_state() is not None
    assert not state.load_state().get("action_failures")
    assert all(call != tuple(menubar.REFRESH) for call in fake_runner.calls)


@pytest.mark.parametrize("process_output,rc", [("", 0), ("", 124), ("invalid process row", 0)])
def test_stopped_or_unreadable_swiftbar_never_opens_url(installed, fake_runner, process_output, rc):
    i18n.set_language("en")
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        process_output, "timeout" if rc else "", rc
    )
    with menubar.status_changes():
        state.save_state(state.new_state())
    assert tuple(menubar.REFRESH) not in fake_runner.calls
    assert not state.load_state().get("action_failures")
    log = (state._path().parent / "travel-mode.log").read_text()
    assert '"kind": "observation"' in log
    assert "Skipped menu bar refresh" in log
    assert (
        "SwiftBar is not running" if not process_output and not rc else "Cannot check whether SwiftBar"
    ) in log


@pytest.mark.parametrize("identity", ["main", "other-bundle", "helper", "argument", "zombie"])
def test_refresh_requires_live_main_executable_and_matching_bundle(
    installed, swiftbar_process, fake_runner, identity
):
    executable = str(swiftbar_process / "Contents/MacOS/SwiftBar")
    if identity == "other-bundle":
        path = swiftbar_process / "Contents/Info.plist"
        info = plistlib.loads(path.read_bytes())
        info["CFBundleIdentifier"] = "org.example.other"
        path.write_bytes(plistlib.dumps(info))
    elif identity == "helper":
        executable = str(swiftbar_process / "Contents/Helpers/SwiftBar")
    elif identity == "argument":
        executable = "/bin/tool --app " + executable
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        f"1234 1 {'Z' if identity == 'zombie' else 'S'} Thu Oct 15 12:00:00 2026 {executable}\n", "", 0
    )
    menubar.refresh()
    assert (tuple(menubar.REFRESH) in fake_runner.calls) is (identity == "main")


def test_nested_scope_coalesces_and_readonly_scope_does_not_refresh(installed, fake_runner):
    with menubar.status_changes(), menubar.status_changes():
        state.save_state(state.new_state())
    assert fake_runner.calls.count(tuple(menubar.REFRESH)) == 1
    fake_runner.calls.clear()
    with menubar.status_changes():
        state.load_state()
    assert not fake_runner.calls


def test_dry_run_only_prints_refresh_plan(installed, fake_runner, darwin_platform, capsys, tmp_path):
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    assert cli.main(["on", "--scenario", "work", "--yes", "--no-measure", "--dry-run"]) == 0
    assert "swiftbar://refreshplugin?name=travel-mode" in capsys.readouterr().out
    assert tuple(menubar.REFRESH) not in fake_runner.calls
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("running", [True, False])
def test_visibility_refresh_observes_committed_config_and_respects_stopped_swiftbar(
    installed, fake_runner, running
):
    if not running:
        fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult("", "", 0)
    original = shell.get_runner()
    observed = []

    def runner(command, timeout=None, interactive=False):
        if command == menubar.REFRESH:
            observed.append(load_config().menubar.show)
        return original(command, timeout, interactive)

    shell.set_runner(runner)
    write_menubar("session")
    write_menubar("always")
    assert observed == (["session", "always"] if running else [])
    assert state.load_state() is None

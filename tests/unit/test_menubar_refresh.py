"""Finite post-operation notifications cannot affect recovery or action counts."""

import asyncio
from pathlib import Path

import pytest

from travel_mode import cli, menubar, state
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


@pytest.fixture
def installed(tmp_path, fake_runner):
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / menubar.FILENAME).write_bytes(menubar.payload())
    fake_runner.responses[tuple(menubar.PREFERENCE)] = CmdResult(str(folder), "", 0)
    fake_runner.responses[tuple(menubar.REFRESH)] = CmdResult("", "", 0)
    return folder


def test_cli_on_off_notify_once_after_committed_state(installed, fake_runner, darwin_platform, capsys):
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    observations = []

    def runner(command, timeout=None, interactive=False):
        if command == menubar.REFRESH:
            observations.append(state.load_state() is not None)
        return fake_runner(command, timeout, interactive)

    shell.set_runner(runner)
    assert cli.main(["on", "--scenario", "work", "--yes", "--no-measure"]) == 0
    counts = state.load_state()["session"]["actions"]
    assert counts["attempted"] == counts["succeeded"] and counts["failed"] == 0
    assert cli.main(["off"]) == 0
    assert observations == [True, False]
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

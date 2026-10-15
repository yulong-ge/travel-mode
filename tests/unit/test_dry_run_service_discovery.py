"""Dry-run keeps read-only discovery roots while isolating every write destination."""

import json
import plistlib
from pathlib import Path

import pytest

from travel_mode import categories, cli
from travel_mode.actions import runtime
from travel_mode.config import load_config
from travel_mode.flows.restore import mode_off
from travel_mode.i18n import set_language
from travel_mode.system import launchd
from travel_mode.system.shell import CmdResult, dry_run_commands, dry_run_state_dir


@pytest.mark.parametrize("scenario", ["work", "away"])
@pytest.mark.parametrize("unreadable_daemon", [False, True])
def test_dry_run_plans_all_configured_user_agents_and_off_reloads(
    scenario, unreadable_daemon, tmp_paths, monkeypatch, fake_runner, capsys
):
    set_language("en")
    directory = tmp_paths / "Library/LaunchAgents"
    directory.mkdir(parents=True)
    labels = ("org.example.worker", "org.example.backup", "org.example.keep", "org.example.remote")
    for label in labels:
        (directory / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label}))
    daemon = tmp_paths / "remote-daemon.plist"
    daemon.write_bytes(plistlib.dumps({"Label": "org.example.remote.daemon"}))
    config = tmp_paths / "config.toml"
    config.write_text(
        '[apps]\ndefault="skip"\n[[apps.with_daemon]]\nname="Remote Support"\n'
        f'system_daemons=["{daemon}"]\nuser_agents=["{directory / (labels[3] + ".plist")}"]\n'
        '[services]\nunload=["org.example.*", "backup"]\nkeep=["*.keep"]\n'
    )
    before = config.read_bytes(), config.stat().st_mtime_ns
    monkeypatch.setattr(
        categories,
        "rules",
        lambda: (
            {
                "id": "fixture-backup",
                "category": "backup",
                "name": "Fixture Backup",
                "action": "unload",
                "labels": ["org.example.*"],
                "apps": [],
                "bundle_ids": [],
                "executables": [],
            },
        ),
    )
    fake_runner.responses[("launchctl", "list")] = CmdResult(
        "\n".join(f"{'-' if i % 2 else '900001'} 0 {label}" for i, label in enumerate(labels)), "", 0
    )
    fake_runner.responses[("sudo", "-n", "launchctl", "list")] = CmdResult(
        "", "fixture: credentials unavailable", 1 if unreadable_daemon else 0
    )
    fake_runner.responses[("pmset", "-g", "assertions")] = CmdResult(
        'pid 900001(remote-helper): PreventSystemSleep named: "Remote Support"\n', "", 0
    )
    expected = {row["plist_path"] for row in launchd.svc_scan(load_config())["unloadable"]}
    assert expected == {str(directory / f"{label}.plist") for label in labels if not label.endswith(".keep")}
    captured = {}
    finish = runtime._finish_dry_run

    def record_plan():
        captured["commands"] = dry_run_commands()
        captured["state"] = dry_run_state_dir() / "state.json"
        finish()

    monkeypatch.setattr(runtime, "_finish_dry_run", record_plan)
    rc = cli.main(["--dry-run", "--scenario", scenario, "--yes"])
    assert rc == (1 if unreadable_daemon and scenario == "away" else 0)
    planned = [command[2] for command in captured["commands"] if command[:2] == ["launchctl", "unload"]]
    assert set(planned) == expected
    assert len(planned) == len(expected)
    snapshot = json.loads(captured["state"].read_text())
    assert set(snapshot["unloaded_svcs"]) == expected
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    assert not (tmp_paths / "state.json").exists()
    monkeypatch.setattr(runtime, "_finish_dry_run", finish)
    monkeypatch.setenv("TRAVEL_MODE_STATE", str(captured["state"]))
    fake_runner.responses[("launchctl", "list")] = CmdResult("", "", 0)
    fake_runner.responses[("launchctl", "load")] = CmdResult("", "", 0)
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    fake_runner.calls.clear()
    assert mode_off() == 0
    assert {call[2] for call in fake_runner.calls if call[:2] == ("launchctl", "load")} == expected
    assert not captured["state"].exists()
    capsys.readouterr()


def test_dry_run_scan_keeps_original_user_directory_without_changing_home_back(tmp_paths):
    roots = launchd.scan_directories()
    observed = {}

    def dispatch(argv):
        observed["home"] = Path.home()
        observed["roots"] = launchd.scan_directories()
        return 0

    assert runtime.command_lifecycle(["--dry-run"], dispatch) == 0
    assert observed["home"] != tmp_paths
    assert observed["roots"] == roots
    assert Path.home() == tmp_paths


def test_dry_run_discovery_scope_restores_after_error(tmp_paths):
    roots = launchd.scan_directories()

    def fail(argv):
        assert Path.home() != tmp_paths
        assert launchd.scan_directories() == roots
        raise RuntimeError("fixture failure")

    with pytest.raises(RuntimeError, match="fixture failure"):
        runtime.command_lifecycle(["--dry-run"], fail)
    assert Path.home() == tmp_paths
    assert launchd.scan_directories() == roots

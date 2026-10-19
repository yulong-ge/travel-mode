"""Regression coverage for isolated CLI and Textual action safety."""

import asyncio
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from textual.widgets import Button

import travel_mode.actions.runtime as runtime
import travel_mode.config as config_api
import travel_mode.flows.check as flow_check
import travel_mode.tui.app as tui_app
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.cli import main
from travel_mode.config import AppsConfig, Config, DaemonApp
from travel_mode.i18n import get_language, set_language
from travel_mode.paths import config_path, state_path
from travel_mode.state import StateError
from travel_mode.system import apps as system_apps
from travel_mode.system import daemon_apps, launchd, platform, shell
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp


@pytest.fixture
def cli_platform(monkeypatch):
    monkeypatch.setattr(platform, "ensure_macos", lambda: None)
    monkeypatch.setattr(platform, "detect_capabilities", lambda: None)


@pytest.fixture(autouse=True)
def restore_language():
    previous = get_language()
    try:
        yield
    finally:
        set_language(previous)


def test_cli_dry_run_migration_preserves_source_files(cli_platform, tmp_paths, monkeypatch):
    old_config = tmp_paths / ".travel-mode.conf"
    old_state = tmp_paths / ".travel-mode.state"
    old_config.write_text("default: ask\nquit: Demo\n")
    old_state.write_text('FROZEN_APPS="/Applications/Demo.app"\n')
    before = old_config.read_bytes(), old_state.read_bytes()
    monkeypatch.setattr(flow_check, "mode_check", lambda: 0)
    try:
        assert main(["check", "--dry-run"]) == 0
        assert old_config.exists() and old_state.exists()
        assert (old_config.read_bytes(), old_state.read_bytes()) == before
        assert not config_path().exists() and not state_path().exists()
    finally:
        runtime._finish_dry_run()


def test_cli_dry_run_thaw_never_signals_underlying_runner(cli_platform, fake_runner):
    fake_runner.responses[("ps", "-axo", "pid,stat,comm")] = CmdResult(
        "777 T /Applications/Demo.app/Contents/MacOS/Demo\n", "", 0
    )
    fake_runner.responses[("kill", "-CONT", "777")] = CmdResult("", "", 0)
    try:
        assert main(["thaw", "--all", "--dry-run"]) == 0
        assert ("kill", "-CONT", "777") not in fake_runner.calls
        assert not shell.is_dry_run()
    finally:
        runtime._finish_dry_run()


def test_cli_dry_run_config_write_preserves_original(cli_platform):
    config_path().write_text('# Original\n[options]\nlanguage = "en"\n')
    original = config_path().read_bytes()
    assert main(["config", "set-language", "zh", "--dry-run"]) == 0
    assert config_path().read_bytes() == original


async def test_tui_failed_thaw_retains_receipt(fake_runner):
    from travel_mode.tui.plan import item, operation

    path = "/Applications/Demo.app"
    fake_runner.responses[("kill",)] = CmdResult("", "not permitted", 2)
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        f"777 1 T Mon Oct 12 09:00:00 2026 {path}/Contents/MacOS/Demo\n", "", 0
    )
    st = {"frozen_apps": [path]}
    row = item("resume", "apps", "Demo", "thaw", "", dict(kind="app", name="Demo", path=path))
    assert not Executor(Context(st, lambda *a: None)).execute(operation(row, "away", st, lambda *a: None))
    assert st["frozen_apps"] == [path]
    assert st["session"]["actions"]["failed"] == 1


def test_tui_missing_merged_snapshot_raises_before_silent_save(tmp_paths):
    app = SimpleNamespace(merged=True, st={}, logline=lambda *args: None, notify=lambda *a, **kw: None)
    with pytest.raises(StateError):
        TravelApp.save(app)


async def test_tui_power_selection_uses_actual_key_and_override(monkeypatch):
    from travel_mode.system.pmset import power_preview

    config = Config(power_overrides={"lowpowermode": "0"})
    preview = power_preview({"b": {"powermode": "1"}}, supported=frozenset({"powermode"}), config=config)
    assert [(r["key"], r["target"]) for r in preview["changes"]] == [("powermode", "0")]


async def test_tui_network_daemon_uses_actual_configured_name(monkeypatch):
    from travel_mode.tui.plan import item, operation

    spec = DaemonApp("Remote Demo", system_daemons=("/tmp/remote.plist",))
    monkeypatch.setattr(config_api, "load_config", lambda: Config(apps=AppsConfig(with_daemon=(spec,))))
    calls = []
    monkeypatch.setattr(
        daemon_apps, "quit_with_daemons", lambda *args: calls.append(args) or ("/Demo.app", True, "ok")
    )
    monkeypatch.setattr(system_apps, "grace_quit", lambda *a: pytest.fail("daemon used generic quit"))
    st = {}
    row = item("remote", "network", spec.name, "quit", "", dict(kind="app", name=spec.name, path="/Demo.app"))
    Executor(Context(st, lambda *a: None)).execute(operation(row, "away", st, lambda *a: None))
    assert calls[0][0] == spec


async def test_tui_batch_completion_does_not_mark_replacement_row(monkeypatch, tmp_paths):
    from tui_plan import select_plan

    config_path().write_text("")
    entered, release = threading.Event(), threading.Event()
    path = str(tmp_paths / "DemoA.app")
    rows = [dict(name="DemoA", path=path, cpu="0", defact="freeze")]
    monkeypatch.setattr(system_apps, "scan_apps", lambda: dict(running=list(rows), nobundle=[]))

    def freeze(path):
        entered.set()
        assert release.wait(10)
        return True

    monkeypatch.setattr(system_apps, "freeze_app", freeze)
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(110, 30)):
        await app.wait_for_idle()
        await select_plan(app, names={"DemoA"}, action="freeze")
        task = asyncio.create_task(app.on_button_pressed(Button.Pressed(app.query_one("#b-quick"))))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            rows[:] = [dict(name="DemoB", path=str(tmp_paths / "DemoB.app"), cpu="0", defact="freeze")]
            app.action_refresh()
            assert app._refresh_after_action
        finally:
            release.set()
            await task
        await app.wait_for_idle()
        assert path in app.st["frozen_apps"]
        assert path in json.loads(state_path().read_text())["frozen_apps"]
        app.view_mode = "plan"
        app.plan_items = []
        app.action_refresh()
        await app.wait_for_idle()
        replacement = next(r for r in app.plan_items if r["name"] == "DemoB")
        assert replacement["state"] == "planned"


@pytest.mark.parametrize("button_id", ["b-quick", "b-off"])
async def test_tui_removed_snapshot_preflight_prevents_mutation(
    button_id, monkeypatch, tmp_paths, fake_runner
):
    from travel_mode.state import new_state, save_state

    config_path().write_text("")
    snapshot = new_state()
    snapshot["quit_apps"] = [str(tmp_paths / "Demo.app")]
    save_state(snapshot)
    monkeypatch.setattr(system_apps, "APP_DIRS", [])
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": [], "kept": []})
    monkeypatch.setattr(flow_check, "blockers_lines", lambda: flow_check.BlockerLines([]))
    fake_runner.responses[("blueutil", "-p", "0")] = CmdResult("", "", 0)
    fake_runner.responses[("open",)] = CmdResult("", "", 0)
    fake_runner.responses[("sudo", "-n", "launchctl", "list")] = CmdResult("PID\tStatus\tLabel\n", "", 0)
    app = TravelApp(scenario="away")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.wait_for_idle()
        state_path().unlink()
        try:
            await app.on_button_pressed(Button.Pressed(Button(id=button_id)))
            assert app.is_running and app._exception is None
            assert "StateError" in (tmp_paths / "travel-mode.log").read_text()
        finally:
            await app.wait_for_idle()
            await pilot.pause()
        assert ("blueutil", "-p", "0") not in fake_runner.calls
        assert not any(command[0] == "open" for command in fake_runner.calls)
        assert not state_path().exists()


@pytest.mark.parametrize("quick", [False, True])
async def test_tui_apps_daemon_batch_dispatches_configured_spec(quick, monkeypatch, tmp_paths):
    spec = DaemonApp("Remote Demo", system_daemons=("/tmp/remote.plist",), group="apps")
    config_path().write_text("")
    config = Config(apps=AppsConfig(quit=(spec.name,), with_daemon=(spec,)))
    monkeypatch.setattr(config_api, "load_config", lambda: config)
    monkeypatch.setattr(system_apps, "load_conf", lambda: ({spec.name: "quit"}, "ask"))
    monkeypatch.setattr(system_apps, "APP_DIRS", [])
    monkeypatch.setattr(
        system_apps,
        "scan_apps",
        lambda: {
            "running": [
                {"name": spec.name, "path": str(tmp_paths / "Remote Demo.app"), "cpu": "0", "defact": "quit"}
            ],
            "nobundle": [],
        },
    )
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": [], "kept": []})
    monkeypatch.setattr(flow_check, "blockers_lines", lambda: flow_check.BlockerLines([]))
    calls = []
    monkeypatch.setattr(
        daemon_apps, "quit_with_daemons", lambda *args: calls.append(args[0]) or ("/Demo.app", True, "ok")
    )
    monkeypatch.setattr(
        system_apps,
        "grace_quit",
        lambda *args: pytest.fail("daemon app bypassed configured service shutdown"),
    )
    app = TravelApp(scenario="away")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.wait_for_idle()
        from tui_plan import apply_plan

        await apply_plan(pilot, group="apps" if quick else None, names=None if quick else {spec.name})
        assert calls == [spec]


def test_cli_dry_run_tui_language_write_uses_configuration_copy(cli_platform, monkeypatch):
    from travel_mode.config import write_language

    config_path().write_text('# Keep me\n[options]\nlanguage = "en"\n')
    original = config_path().read_bytes()
    source = config_path()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def tui(*, dry_run, measure):
        assert dry_run
        assert measure is None
        assert config_path() != source
        write_language("zh")
        return 0

    monkeypatch.setattr(tui_app, "tui", tui)
    assert main(["on", "--dry-run"]) == 0
    assert config_path() == source and source.read_bytes() == original

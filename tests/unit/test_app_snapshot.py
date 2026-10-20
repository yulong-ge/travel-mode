"""行为契约：安装位置、匹配优先级、主进程边界和单轮快照。"""

import plistlib

import pytest

from travel_mode.config import AppsConfig, Config, NetworkConfig
from travel_mode.network_apps import network_scan
from travel_mode.system import apps as system_apps
from travel_mode.system.app_snapshot import AppIndex, AppSnapshot, current_snapshot, snapshot_scope
from travel_mode.system.shell import CmdResult


def bundle(path, **metadata):
    (path / "Contents").mkdir(parents=True)
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps(metadata))
    return str(path)


def test_index_aliases_nested_prefix_and_extra_locations(tmp_paths, fake_runner):
    root = tmp_paths / "Applications"
    exact = bundle(root / "Editor.app", CFBundleDisplayName="Other")
    nested = bundle(
        root / "Utilities/Nested.app", CFBundleName="Friendly", CFBundleIdentifier="org.example.tool"
    )
    studio = bundle(root / "Demo Studio.app")
    vendor = bundle(tmp_paths / "Library/arbitrary/tools/Remote.app", CFBundleDisplayName="Remote Access")
    fake_runner.responses[("mdfind",)] = CmdResult(vendor + "\n", "", 0)
    index = AppIndex([str(root)])
    assert index.find("Editor") == exact
    assert index.find("Other") == exact
    assert index.find("Friendly") == nested
    assert index.find("org.example.tool") == nested
    assert index.find("Demo") == studio
    assert index.find("Remote Access") == vendor
    assert index.find("mote") == vendor
    assert index.find("Uninstalled") == ""


def test_shared_snapshot_main_process_cpu_and_resident_semantics(tmp_paths, monkeypatch, fake_runner):
    root = tmp_paths / "Applications"
    editor = bundle(root / "Editor.app")
    helper = bundle(root / "Helper Only.app")
    remote = bundle(root / "Remote.app", CFBundleIdentifier="org.example.remote")
    extra = bundle(tmp_paths / "Library/arbitrary/Remote.app", CFBundleIdentifier="org.example.remote")
    fake_runner.responses[("mdfind",)] = CmdResult(extra + "\n", "", 0)
    fake_runner.responses[("ps",)] = CmdResult(
        f"10 1.2 {editor}/Contents/MacOS/Editor\n"
        f"11 3.4 {editor}/Contents/Frameworks/helper\n"
        f"12 0.5 {helper}/Contents/Helpers/helper\n"
        f"13 0.7 {remote}/Contents/Resources/resident\n"
        f"14 0.8 {extra}/Contents/Helpers/resident\n",
        "",
        0,
    )
    monkeypatch.setattr(system_apps, "APP_DIRS", [str(root)])
    config = Config(apps=AppsConfig(freeze=("Editor", "Helper Only")), network=NetworkConfig(("Remote",)))
    monkeypatch.setattr(system_apps, "load_config", lambda: config)
    monkeypatch.setattr(system_apps, "load_conf", lambda: (config.apps.actions, config.apps.default))
    with snapshot_scope():
        scan = system_apps.scan_apps()
        rows = network_scan(config)
        assert system_apps.app_running(editor)
        assert system_apps.main_proc_alive(editor)
        assert not system_apps.app_running(helper)
        assert not system_apps.main_proc_alive(helper)
        assert system_apps.bundle_alive(helper)
    assert [row["name"] for row in scan["running"]] == ["Editor"]
    assert scan["running"][0]["cpu"] == "3.4"
    assert rows[0]["status"] == "background" and rows[0]["resident"]
    assert rows[0]["cpu"] == 1.5
    assert sum(call[0] == "ps" for call in fake_runner.calls) == 1
    assert sum(call[0] == "mdfind" for call in fake_runner.calls) == 1
    assert not any(call[0] == "pgrep" for call in fake_runner.calls)
    assert current_snapshot() is None
    # 下一轮读取最新进程，旧快照不得成为永久缓存。
    fake_runner.responses[("ps",)] = CmdResult("", "", 0)
    assert system_apps.scan_apps()["running"] == []


@pytest.mark.parametrize("command,rc", [("mdfind", 124), ("ps", 1)])
def test_snapshot_command_failure_is_explicit(command, rc, fake_runner):
    fake_runner.responses[(command,)] = CmdResult("", "probe failed", rc)
    with pytest.raises(RuntimeError, match="probe failed"):
        AppSnapshot.capture([])


def test_snapshot_invalid_process_row_is_explicit(fake_runner):
    fake_runner.responses[("ps",)] = CmdResult("broken row\n", "", 0)
    with pytest.raises(ValueError, match="Invalid ps application snapshot row"):
        AppSnapshot.capture([])


def test_extra_app_search_directories_resolve_nonstandard_bundle(tmp_path, monkeypatch, fake_runner):
    from travel_mode.system.apps import app_path

    root = tmp_path / "custom-apps"
    bundle = root / "Extra Demo.app"
    bundle.mkdir(parents=True)
    monkeypatch.setenv("TRAVEL_MODE_APP_DIRS", str(root))
    assert app_path("Extra Demo") == str(bundle)
    from travel_mode.system.app_snapshot import snapshot_scope

    with snapshot_scope() as snapshot:
        assert snapshot.index.find("Extra Demo") == str(bundle)


@pytest.mark.parametrize("value", ["", "relative", "/absolute:", ":/absolute"])
def test_extra_app_search_directories_reject_invalid_entries(value, monkeypatch):
    from travel_mode.system.apps import app_directories

    monkeypatch.setenv("TRAVEL_MODE_APP_DIRS", value)
    with pytest.raises(ValueError, match="TRAVEL_MODE_APP_DIRS"):
        app_directories()


async def test_tui_snapshot_honors_extra_app_dirs(monkeypatch, tmp_path):
    from travel_mode.system.app_snapshot import AppSnapshot
    from travel_mode.tui.app import TravelApp

    extra = str(tmp_path / "extra")
    monkeypatch.setenv("TRAVEL_MODE_APP_DIRS", extra)
    observed = []
    capture = AppSnapshot.capture

    def record(directories):
        observed.append(directories)
        return capture(directories)

    monkeypatch.setattr(AppSnapshot, "capture", record)
    app = TravelApp(scenario="away")
    async with app.run_test() as pilot:
        await app.wait_for_idle()
        await pilot.pause()
    assert observed and all(extra in dirs for dirs in observed)

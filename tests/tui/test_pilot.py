"""Textual Pilot: read-only sampling and separate fake-runner action coverage."""

import json
import shutil
import sys
import threading
from itertools import pairwise
from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Footer, TabbedContent

import travel_mode.flows.check as flow_check
import travel_mode.flows.common as common
import travel_mode.state as state_api
from travel_mode import state
from travel_mode.i18n import get_language, set_language
from travel_mode.i18n import t as translate
from travel_mode.paths import config_path, state_path
from travel_mode.system import apps as system_apps
from travel_mode.system import launchd, network
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from user_data_snapshot import assert_unchanged, snapshot_user_data


@pytest.fixture
def demo_app(monkeypatch, tmp_paths):
    app_path = str(tmp_paths / "Demo.app")
    monkeypatch.setattr(
        system_apps,
        "scan_apps",
        lambda: {
            "running": [{"name": "Demo", "path": app_path, "cpu": "1", "defact": "freeze"}],
            "nobundle": [],
        },
    )
    monkeypatch.setattr(
        launchd,
        "svc_scan",
        lambda config=None: {"unloadable": [], "kept": [], "candidates": [], "invalid": []},
    )
    monkeypatch.setattr(flow_check, "blockers_lines", lambda: flow_check.BlockerLines([]))
    return app_path


@pytest.fixture
def readonly_tui(monkeypatch, tmp_paths):
    config_path().write_text("", encoding="utf-8")

    def forbid_save(*args, **kwargs):
        pytest.fail("Read-only TUI smoke test called save_state")

    monkeypatch.setattr(state, "save_state", forbid_save)
    monkeypatch.setattr(state_api, "save_state", forbid_save)
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "blueutil" else real_which(name))
    before_config = config_path().read_bytes(), config_path().stat().st_mtime_ns
    before_xdg = snapshot_user_data(tmp_paths)
    try:
        yield
    finally:
        assert not state_path().exists()
        assert (config_path().read_bytes(), config_path().stat().st_mtime_ns) == before_config
        assert_unchanged(before_xdg, snapshot_user_data(tmp_paths))


async def wait_for_dashboard(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    assert app.query_one(TabbedContent).active == "p-dash"
    for selector in ("#power-list", "#apps-box", "#dash-status", "#svc-box", "#net-box"):
        assert app.query_one(selector).children


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 45)])
async def test_list_row_buttons_are_compact_and_centered(
    language, size, demo_app, readonly_tui, monkeypatch, restore_language, tmp_paths
):
    from textual.widgets import Button, Label

    set_language(language)
    service = {"label": "org.demo.worker", "pid": "123", "cpu": "1", "plist_path": str(tmp_paths / "a")}
    monkeypatch.setattr(
        launchd, "svc_scan", lambda config=None: {"unloadable": [service], "kept": [], "invalid": []}
    )
    app = TravelApp(scenario="away")
    async with app.run_test(size=size) as pilot:
        await wait_for_dashboard(app, pilot)
        # Mount deterministic running network rows without probing the host.
        await app.build_net(
            [
                dict(
                    name=f"Network Demo {i}",
                    path=f"/fixture/{i}.app",
                    running=True,
                    status="running",
                    cpu=1.0,
                    resident=False,
                )
                for i in range(2)
            ],
            "en0",
            True,
            True,
        )
        for page, box in (("p-apps", "apps-box"), ("p-svc", "svc-box"), ("p-net", "net-box")):
            app.action_goto(page)
            await pilot.pause()
            rows = list(app.query(f"#{box} .row"))
            assert rows
            for row in rows:
                label = row.query_one(Label)
                buttons = list(row.query(Button))
                assert buttons
                assert row.region.height == 1
                assert label.region.height == 1
                for button in buttons:
                    assert button.region.y == label.region.y
                    assert button.region.height == 1
                    assert button.content_region == button.region
            for previous, current in pairwise(rows):
                assert current.region.y == previous.region.bottom
            for row in app.query(f"#{page} .cardrow"):
                label = row.query_one(Label)
                button = row.query_one(Button)
                assert label.region.y == button.region.y
                assert label.region.height == button.region.height == row.region.height
                assert button.styles.content_align_vertical == "middle"


@pytest.mark.asyncio
async def test_tui_pilot_read_only(demo_app, readonly_tui):
    app = TravelApp(scenario="away")
    async with app.run_test(size=(110, 30)) as pilot:
        await wait_for_dashboard(app, pilot)
        audit = app.query_one("#dash-audit")
        audit_txt = str(audit.content) if audit.content is not None else ""
        assert "InternalBattery" in audit_txt or "CPU" in audit_txt or "电池供电" in audit_txt

        tb = app.query_one(TabbedContent)
        for pane in ("p-apps", "p-svc", "p-net", "p-power"):
            tb.active = pane
            await pilot.pause()
        assert app.pw_boxes
        _, _, checkbox = app.pw_boxes[0]
        await pilot.click(checkbox)
        await pilot.pause()
        assert checkbox.value is False

        tb.active = "p-dash"
        await pilot.pause(0.5)
        scroll = app.query_one("#p-dash VerticalScroll", VerticalScroll)
        assert scroll.virtual_size.height > scroll.size.height
        scroll.scroll_end(animate=False)
        await pilot.pause(0.5)
        assert scroll.scroll_offset.y > 0


@pytest.mark.asyncio
async def test_tui_language_toggle_recomposes_and_persists(demo_app, monkeypatch, tmp_paths):
    set_language("en")
    app = TravelApp(scenario="away")
    async with app.run_test(size=(110, 30)) as pilot:
        await wait_for_dashboard(app, pilot)
        assert "Language" in str(app._bindings.key_to_bindings["l"][0].description)
        await pilot.press("l")
        await pilot.pause()
        assert get_language() == "zh"
        assert "语言" in str(app._bindings.key_to_bindings["l"][0].description)
        assert len(app.query(Footer)) == 1
        assert not app.query("#lang-footer")
        assert 'language = "zh"' in config_path().read_text(encoding="utf-8")
    set_language("zh")


@pytest.mark.asyncio
async def test_tui_pilot_freeze_saves_only_isolated_state(monkeypatch, tmp_paths, fake_runner, demo_app):
    stale_path = tmp_paths / "stale-import-state.json"
    stale_path.write_text('{"frozen_apps": ["/Applications/Existing.app"]}\n', encoding="utf-8")
    before_stale = stale_path.read_bytes(), stale_path.stat().st_mtime_ns
    monkeypatch.setattr(common, "STATE_PATH", stale_path)
    fake_runner.responses[("kill", "-STOP", "123")] = CmdResult("", "", 0)
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        f"123 1 S Mon Oct 12 09:00:00 2026 {demo_app}/Contents/MacOS/Demo\n", "", 0
    )
    app = TravelApp(scenario="away")
    assert not app.merged
    async with app.run_test(size=(110, 30)) as pilot:
        await wait_for_dashboard(app, pilot)
        rows = [i for i, row in app.app_rows.items() if row["path"] == demo_app]
        assert len(rows) == 1
        index = rows[0]
        app.query_one(TabbedContent).active = "p-apps"
        await pilot.pause()
        await pilot.click(f"#btn-{index}-f")
        await pilot.pause()
        assert app.app_rows[index]["done"]

    assert state_path() == tmp_paths / "state.json"
    assert state.load_state()["frozen_apps"] == [demo_app]
    assert (stale_path.read_bytes(), stale_path.stat().st_mtime_ns) == before_stale
    assert ("kill", "-STOP", "123") in fake_runner.calls
    assert not any(command[0] == "pkill" for command in fake_runner.calls)
    assert state.load_state()["frozen_pids"][0]["pid"] == 123


@pytest.mark.macos
@pytest.mark.skipif(sys.platform != "darwin", reason="requires a macOS host")
@pytest.mark.asyncio
async def test_tui_pilot_macos_read_only(readonly_tui):
    app = TravelApp(scenario="away")
    async with app.run_test(size=(110, 30)) as pilot:
        await wait_for_dashboard(app, pilot)


@pytest.fixture
def restore_language():
    """Pilot key presses can toggle the process-global UI language."""
    previous = get_language()
    yield
    set_language(previous)


@pytest.mark.asyncio
async def test_tui_network_page_bottom_controls_stay_visible(demo_app, readonly_tui):
    app = TravelApp(scenario="away")
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_dashboard(app, pilot)
        tb = app.query_one(TabbedContent)
        tb.active = "p-net"
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        screen = app.screen.region
        # Wi-Fi and restore controls live in the pinned bottom cards, never under the log
        for selector in ("#b-off", "#b-wifi"):
            assert screen.contains_region(app.query_one(selector).region), selector
        assert app.query_one("#net-actions").children
        # the disconnect list scrolls on its own instead of pushing controls away
        scroll = app.query_one("#net-scroll", VerticalScroll)
        assert scroll.virtual_size.height >= scroll.size.height


@pytest.mark.parametrize("size", [(140, 45), (80, 24)])
@pytest.mark.parametrize(
    "key,panel",
    [("1", "dash-status"), ("2", "power-list"), ("3", "apps-box"), ("4", "svc-box"), ("5", "net-box")],
)
@pytest.mark.asyncio
async def test_first_active_tab_renders_after_worker_without_switching(
    key, panel, size, demo_app, readonly_tui, monkeypatch
):
    import asyncio

    from travel_mode import network_apps

    started, release = threading.Event(), threading.Event()

    def slow_network():
        assert threading.current_thread() is not threading.main_thread()
        started.set()
        assert release.wait(10), "test did not release network sampling"
        return [
            {
                "name": "Demo",
                "path": None,
                "running": False,
                "status": "missing",
                "cpu": None,
                "resident": False,
            }
        ]

    monkeypatch.setattr(network_apps, "network_scan", slow_network)
    monkeypatch.setattr(flow_check, "audit_lines", lambda: ["Audit complete"])
    app = TravelApp(scenario="away")
    try:
        async with app.run_test(size=size) as pilot:
            assert await asyncio.to_thread(started.wait, 5)
            # This key must respond while host sampling is blocked; it was frozen on old code.
            await pilot.press(key)
            tab = app.query_one(TabbedContent).active
            assert tab == ("p-dash", "p-power", "p-apps", "p-svc", "p-net")[int(key) - 1]
            assert not app.refresh_complete
            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.refresh_complete
            assert app.query_one(TabbedContent).active == tab
            body = app.query_one("#" + panel)
            content = next(iter(body.query("Label, Static, Checkbox, Button")))
            for widget in (body, content):
                visible = widget.region.intersection(app.screen.region)
                for parent in widget.ancestors:
                    if parent is app:
                        break
                    assert parent.display and parent.visible
                    visible = visible.intersection(parent.content_region)
                assert widget.region.height > 0
                assert visible.height > 0 and visible.width > 0
            assert app.screen.region.contains_region(content.region)
    finally:
        release.set()


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.asyncio
async def test_network_hint_uses_actual_services_tab_title(
    language, demo_app, readonly_tui, restore_language
):
    set_language(language)
    app = TravelApp(scenario="away")
    async with app.run_test(size=(140, 45)) as pilot:
        await wait_for_dashboard(app, pilot)
        title = str(app.query_one(TabbedContent).get_tab("p-svc").label)
        assert title == translate("services.title")
        hint = translate("network.services_hint", tab=title)
        assert title in hint
        assert any(str(label.content) == hint for label in app.query("#net-actions Label"))


@pytest.mark.asyncio
async def test_debug_telemetry_survives_recompose_and_empty_active(
    demo_app, tmp_paths, monkeypatch, restore_language
):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    import tui_debug

    telemetry = tmp_paths / "layout.json"
    app = tui_debug.DebugApp(telemetry, scenario="away")
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_dashboard(app, pilot)
        tb = app.query_one(TabbedContent)
        # TabbedContent.active is a reactive defaulting to "" (also set on
        # Tabs.Cleared); recompose windows expose it. The tick must record a lint
        # issue, never raise KeyError('') and kill the TUI under observation.
        tb.active = ""
        app.write_layout()
        data = json.loads(telemetry.read_text())
        assert data["active"] == ""
        assert {"no-active-panel"} <= {issue["kind"] for issue in data["issues"]}

        tb.active = "p-dash"
        await pilot.press("l")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.write_layout()
        data = json.loads(telemetry.read_text())
        assert data["active"] in {"p-dash", "p-power", "p-apps", "p-svc", "p-net"}
        assert data["ready"]
        assert not any(issue["kind"] == "telemetry-error" for issue in data["issues"])


@pytest.mark.asyncio
async def test_debug_frame_records_atomic_content_after_worker_completion(demo_app, tmp_paths, monkeypatch):
    import asyncio

    from travel_mode import network_apps

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import tui_debug

    sampled, resume_sample = threading.Event(), threading.Event()
    pending, resume_apply = asyncio.Event(), asyncio.Event()

    def network_sample():
        sampled.set()
        assert resume_sample.wait(10)
        return []

    monkeypatch.setattr(network_apps, "network_scan", network_sample)
    monkeypatch.setattr(flow_check, "audit_lines", lambda: [])
    app = tui_debug.DebugApp(tmp_paths / "layout.json", scenario="away")
    build_net = app.build_net
    rendered = asyncio.Event()
    write_layout = app.write_layout

    def record_frame():
        if write_layout() and json.loads(app.telemetry.read_text())["refresh_complete"]:
            rendered.set()

    monkeypatch.setattr(app, "write_layout", record_frame)

    async def delayed_net(*args):
        app.logline("dim", "UI callback pending")
        pending.set()
        await resume_apply.wait()
        await build_net(*args)

    monkeypatch.setattr(app, "build_net", delayed_net)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            assert await asyncio.to_thread(sampled.wait, 5)
            await pilot.press("4")
            resume_sample.set()
            await asyncio.wait_for(pending.wait(), 5)
            layout = json.loads(app.telemetry.read_text())
            assert layout["active"] == "p-svc"
            assert not layout["refresh_complete"]
            # 网络页的原子挂载不应阻止已经完成的服务页显示。
            assert layout["content_visible"]
            assert "p-svc" in app.pages_ready
            assert "p-net" not in app.pages_ready
            assert app._batch_count > 0
            rendered.clear()
            resume_apply.set()
            await app.workers.wait_for_complete()
            await asyncio.wait_for(rendered.wait(), 5)
            layout = json.loads(app.telemetry.read_text())
            assert layout["refresh_complete"]
            assert layout["body_visible"] and layout["content_visible"]
    finally:
        resume_sample.set()
        resume_apply.set()


@pytest.mark.asyncio
async def test_language_recompose_rejects_cancelled_refresh_payload(
    demo_app, tmp_paths, monkeypatch, restore_language
):
    import asyncio

    from travel_mode import network_apps

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import tui_debug

    set_language("zh")
    config_path().write_text("")
    started, release, returned = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def network_sample():
        old = not started.is_set()
        name = "Stale" if old else "Current"
        if old:
            started.set()
            assert release.wait(10)
            returned.set()
        return [
            {
                "name": name,
                "path": None,
                "running": False,
                "status": "missing",
                "cpu": None,
                "resident": False,
            }
        ]

    def wifi_sample():
        return ""

    monkeypatch.setattr(network_apps, "network_scan", network_sample)
    monkeypatch.setattr(network, "wifi_dev", wifi_sample)
    monkeypatch.setattr(flow_check, "audit_lines", lambda: [])
    app = tui_debug.DebugApp(tmp_paths / "layout.json", scenario="away")
    build_net = app.build_net

    async def record_net(network, *args):
        calls.append(network[0]["name"])
        await build_net(network, *args)

    monkeypatch.setattr(app, "build_net", record_net)
    try:
        async with app.run_test(size=(120, 36)) as pilot:
            assert await asyncio.to_thread(started.wait, 5)
            await pilot.press("l")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert get_language() == "en" and app.refresh_complete
            assert calls == ["Current"]
            release.set()
            assert await asyncio.to_thread(returned.wait, 5)
            await pilot.pause()
            assert calls == ["Current"]
            assert app.query_one("#dash-status").children
            assert not state_path().exists()
            assert 'language = "en"' in config_path().read_text()
    finally:
        release.set()


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.asyncio
async def test_pages_load_independently_while_app_scan_is_pending(
    language, demo_app, readonly_tui, monkeypatch, restore_language, fake_runner
):
    import asyncio

    set_language(language)
    started, release = threading.Event(), threading.Event()
    original = system_apps.scan_apps

    def slow_apps():
        assert threading.current_thread() is not threading.main_thread()
        started.set()
        assert release.wait(10), "app scan was not released"
        return original()

    monkeypatch.setattr(system_apps, "scan_apps", slow_apps)
    app = TravelApp(scenario="away")
    try:
        async with app.run_test(size=(140, 45)) as pilot:
            assert await asyncio.to_thread(started.wait, 5)
            await pilot.press("3")
            await pilot.pause()
            body = app.query_one("#apps-box")
            assert [str(label.content) for label in body.query("Label")] == [translate("impl.scanning")]
            assert "p-apps" not in app.pages_ready
            # 快速页和网络页已完成，不能等应用扫描。
            for page in ("p-dash", "p-power", "p-svc", "p-net"):
                assert page in app.pages_ready
            assert app.pw_boxes
            await pilot.press("4")
            assert app.query_one("#svc-box").children
            assert all(
                str(label.content) != translate("impl.scanning") for label in app.query("#svc-box Label")
            )
            await pilot.press("3")
            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.refresh_complete
            assert app.query_one(TabbedContent).active == "p-apps"
            assert any("Demo" in str(label.content) for label in body.query("Label"))
            assert all(str(label.content) != translate("impl.scanning") for label in body.query("Label"))
            assert body.region.height > 0
            assert app.screen.region.contains_region(next(iter(body.query("Label"))).region)
            assert sum(call[0] == "mdfind" for call in fake_runner.calls) == 1
            assert sum(call == ("ps", "-axo", "pid=,%cpu=,command=") for call in fake_runner.calls) == 1
    finally:
        release.set()


@pytest.mark.asyncio
async def test_every_page_has_initial_loading_placeholder(
    readonly_tui, fake_runner, monkeypatch, restore_language
):
    from travel_mode.system.shell import set_runner

    set_language("zh")
    app = TravelApp(scenario="away")
    release = threading.Event()

    def delayed_runner(command, timeout=None, interactive=False):
        assert release.wait(10), "initial scans were not released"
        return fake_runner(command, timeout, interactive)

    set_runner(delayed_runner)
    # 索引只读取隔离目录，Runner 延迟控制所有采样完成时机。
    monkeypatch.setattr(system_apps, "APP_DIRS", [])
    try:
        async with app.run_test(size=(140, 45)) as pilot:
            for key, selector in enumerate(
                ("#dash-status", "#power-list", "#apps-box", "#svc-box", "#net-box"), 1
            ):
                await pilot.press(str(key))
                body = app.query_one(selector)
                labels = list(body.query("Label"))
                assert [str(label.content) for label in labels] == [translate("impl.scanning")]
                assert body.region.height > 0
                assert app.screen.region.contains_region(labels[0].region)
            assert not app.refresh_complete
            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.refresh_complete
    finally:
        release.set()


@pytest.mark.asyncio
async def test_stale_hidden_pane_focus_does_not_change_current_page(demo_app, readonly_tui):
    from textual.widgets import TabPane

    app = TravelApp(scenario="away")
    async with app.run_test(size=(140, 45)) as pilot:
        await wait_for_dashboard(app, pilot)
        await pilot.press("3")
        tabs = app.query_one(TabbedContent)
        tabs.post_message(TabPane.Focused(app.query_one("#p-dash", TabPane)))
        await pilot.pause()
        assert tabs.active == "p-apps"


@pytest.mark.asyncio
async def test_dashboard_counts_update_when_slow_apps_arrive(
    demo_app, readonly_tui, monkeypatch, restore_language
):
    import asyncio

    set_language("en")
    started, release = threading.Event(), threading.Event()
    original = system_apps.scan_apps

    def slow_apps():
        started.set()
        assert release.wait(10)
        return original()

    monkeypatch.setattr(system_apps, "scan_apps", slow_apps)
    app = TravelApp(scenario="away")
    try:
        async with app.run_test(size=(140, 45)) as pilot:
            assert await asyncio.to_thread(started.wait, 5)
            await pilot.pause()
            assert "p-dash" in app.pages_ready
            labels = [str(label.content) for label in app.query("#dash-status Label")]
            assert translate("impl.scanning") in labels
            assert not any("High-energy apps: 0" in label for label in labels)
            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            labels = [str(label.content) for label in app.query("#dash-status Label")]
            assert translate("impl.high_energy_apps_running", v0=1, v1="") in labels
            assert translate("impl.scanning") not in labels
    finally:
        release.set()

"""The task-based interface stays responsive and reads one finite discovery snapshot."""

import asyncio
import sys
import threading
from pathlib import Path

import pytest
from textual.widgets import TabbedContent

from travel_mode import state
from travel_mode.i18n import get_language, set_language
from travel_mode.paths import config_path, state_path
from travel_mode.system import apps, launchd
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from tui_interaction import toggle_language
from tui_plan import apply_plan
from user_data_snapshot import assert_unchanged, snapshot_user_data


@pytest.fixture
def demo_app(monkeypatch, tmp_paths):
    path = str(tmp_paths / "Demo.app")
    monkeypatch.setattr(
        apps,
        "scan_apps",
        lambda: dict(running=[dict(name="Demo", path=path, cpu="1", defact="freeze")], nobundle=[]),
    )
    monkeypatch.setattr(
        launchd, "svc_scan", lambda config=None: dict(unloadable=[], kept=[], invalid=[], candidates=[])
    )
    return path


@pytest.fixture
def readonly_tui(monkeypatch, tmp_paths):
    config_path().write_text("")
    before = snapshot_user_data(tmp_paths)
    monkeypatch.setattr(state, "save_state", lambda *a, **k: pytest.fail("read-only view wrote state"))
    yield
    assert not state_path().exists()
    assert_unchanged(before, snapshot_user_data(tmp_paths))


@pytest.fixture
def restore_language():
    previous = get_language()
    yield
    set_language(previous)


async def wait_for_dashboard(app, pilot):
    await app.wait_for_idle()
    assert await app.screen.wait_for_refresh()
    assert app.refresh_complete and app.plan_data
    assert not app.query(TabbedContent)
    assert app.query_one("#plan-list").children


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 45)])
async def test_list_row_buttons_are_compact_and_centered(
    language, size, demo_app, readonly_tui, restore_language
):
    from travel_mode.tui.plan_row import PlanRow

    set_language(language)
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=size) as pilot:
        await wait_for_dashboard(app, pilot)
        rows = list(app.query(PlanRow))
        assert rows
        for row in rows:
            assert row.region.height == (2 if size[0] == 80 else 1)
            label = row.query_one(".plan-name")
            for button in row.query("Button, Checkbox"):
                assert button.region.y == label.region.y
                assert button.region.height == 1
                assert row.content_region.contains_region(button.region)
        assert app.query_one("#key-footer").region.bottom == size[1]


@pytest.mark.asyncio
async def test_tui_pilot_read_only(demo_app, readonly_tui, fake_runner):
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(110, 30)) as pilot:
        await wait_for_dashboard(app, pilot)
        assert str(app.query_one("#dash-battery").content)
        calls = list(fake_runner.calls)
        for key, group in [("2", "power"), ("3", "apps"), ("4", "services"), ("5", "network"), ("1", "all")]:
            await pilot.press(key)
            await app.wait_for_idle()
            assert app.active_filter == group
        assert fake_runner.calls == calls
        assert not app.query_one("#details-drawer").display
        await pilot.press("d")
        assert app.query_one("#details-drawer").display
        await pilot.press("d")
        assert not app.query_one("#details-drawer").display


async def test_tui_language_toggle_recomposes_and_persists(demo_app, restore_language):
    set_language("en")
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(110, 30)) as pilot:
        await wait_for_dashboard(app, pilot)
        await toggle_language(pilot)
        assert get_language() == "zh"
        assert "语言" in str(app.query_one("#key-footer").content)
        assert len(app.query("#key-footer")) == 1
        assert 'language = "zh"' in config_path().read_text()


async def test_tui_pilot_freeze_saves_only_isolated_state(monkeypatch, tmp_paths, fake_runner, demo_app):
    fake_runner.responses[("kill", "-STOP", "123")] = CmdResult("", "", 0)
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        f"123 1 S Mon Oct 12 09:00:00 2026 {demo_app}/Contents/MacOS/Demo\n", "", 0
    )
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(110, 30)) as pilot:
        await wait_for_dashboard(app, pilot)
        await apply_plan(pilot, names={"Demo"}, action="freeze")
        assert next(row for row in app.plan_items if row["name"] == "Demo")["state"] == "done"
    assert state.load_state()["frozen_apps"] == [demo_app]
    assert state.load_state()["frozen_pids"][0]["pid"] == 123
    assert ("kill", "-STOP", "123") in fake_runner.calls
    assert not any(cmd[0] == "pkill" for cmd in fake_runner.calls)


@pytest.mark.macos
@pytest.mark.skipif(sys.platform != "darwin", reason="requires a macOS host")
async def test_tui_pilot_macos_read_only(readonly_tui):
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(110, 30)) as pilot:
        await wait_for_dashboard(app, pilot)


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 45)])
async def test_primary_action_and_footer_stay_pinned(size, demo_app, readonly_tui):
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=size) as pilot:
        await wait_for_dashboard(app, pilot)
        assert app.screen.region.contains_region(app.query_one("#b-quick").region)
        assert app.screen.region.contains_region(app.query_one("#key-footer").region)
        assert len([button for button in app.query("Button.-primary") if button.display]) == 1


@pytest.mark.parametrize(
    "key,group", [("1", "all"), ("2", "power"), ("3", "apps"), ("4", "services"), ("5", "network")]
)
@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
async def test_filter_responds_while_snapshot_is_pending(
    key, group, size, demo_app, readonly_tui, monkeypatch, fake_runner
):
    from travel_mode import network_apps

    entered, release = threading.Event(), threading.Event()

    def sample():
        entered.set()
        assert release.wait(30)
        return []

    monkeypatch.setattr(network_apps, "network_scan", sample)
    app = TravelApp(scenario="away", measure=False)
    try:
        async with app.run_test(size=size) as pilot:
            assert await asyncio.to_thread(entered.wait, 30)
            await pilot.press(key)
            assert app.active_filter == group and not app.refresh_complete
            assert app.query_one("#plan-list").children
            release.set()
            await app.wait_for_idle()
            assert await app.screen.wait_for_refresh()
            assert app.refresh_complete and app.active_filter == group
            assert sum(command[0] == "mdfind" for command in fake_runner.calls) == 1
    finally:
        release.set()


async def test_language_recompose_rejects_cancelled_refresh_payload(demo_app, monkeypatch, restore_language):
    from travel_mode import network_apps

    entered, release, returned = threading.Event(), threading.Event(), threading.Event()

    def sample():
        old = not entered.is_set()
        if old:
            entered.set()
            assert release.wait(30)
            returned.set()
        name = "Stale" if old else "Current"
        return [dict(name=name, path=None, running=False, status="missing", cpu=None, resident=False)]

    monkeypatch.setattr(network_apps, "network_scan", sample)
    app = TravelApp(scenario="away", measure=False)
    try:
        async with app.run_test(size=(120, 36)) as pilot:
            assert await asyncio.to_thread(entered.wait, 30)
            await toggle_language(pilot)
            assert app.refresh_complete
            assert app.plan_data["noise"]["network"][0]["name"] == "Current"
            release.set()
            assert await asyncio.to_thread(returned.wait, 30)
            assert await app.screen.wait_for_refresh()
            assert app.plan_data["noise"]["network"][0]["name"] == "Current"
    finally:
        release.set()


async def test_debug_frame_records_atomic_content_after_worker_completion(demo_app, tmp_paths, monkeypatch):
    from travel_mode import network_apps

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import tui_debug

    entered, release = threading.Event(), threading.Event()

    def sample():
        entered.set()
        assert release.wait(30)
        return []

    app = tui_debug.DebugApp(tmp_paths / "layout.json", scenario="away")
    async with app.run_test(size=(80, 24)):
        await app.wait_for_idle()
        await app.wait_layout(lambda layout: layout["ready"])
        # Admit the controlled refresh after Pilot owns the mounted screen.
        # A direct write_layout() may defer publication until layout commits;
        # reading immediately after it can observe the preceding ready frame.
        monkeypatch.setattr(network_apps, "network_scan", sample)
        try:
            app.action_refresh()
            assert await asyncio.to_thread(entered.wait, 30)
            await app.wait_layout(lambda layout: not layout["refresh_complete"])
            release.set()
            await app.wait_for_idle()
            await app.wait_layout(lambda layout: layout["ready"])
            layout = tui_debug.read_layout(app.telemetry)
            assert layout["ready"] and layout["body_visible"] and layout["content_visible"]
            assert layout["active"] == "plan" and layout["action_generation"] == 0
        finally:
            release.set()

"""Setup and remember wait for explicit completion signals, not timed sleeps."""

import asyncio

import pytest
from textual.widgets import Button, Static

from travel_mode import app_policy
from travel_mode.config import load_config
from travel_mode.i18n import set_language
from travel_mode.tui.app import TravelApp
from travel_mode.tui.setup import ConfirmRemember, SetupApp, SetupScreen


def rows():
    return [
        dict(
            name=name,
            path=f"/Fixture/{name}.app",
            running=True,
            **app_policy.recommend(
                name,
                rule={"file_provider": True} if name == "Google Drive" else None,
                assertion=name == "UURemote",
            ),
        )
        for name in ("WeChat", "Google Drive", "UURemote", "Google Chrome")
    ]


async def ready(app):
    screen = app.screen
    assert isinstance(screen, SetupScreen)
    await asyncio.wait_for(screen.ready.wait(), 10)
    assert await screen.wait_for_refresh()
    return screen


async def press(screen, key, signal):
    signal.clear()
    screen.query_one(key, Button).press()
    await asyncio.wait_for(signal.wait(), 10)


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_setup_tui_edits_columns_reviews_and_preserves_comments(tmp_path, monkeypatch, language):
    set_language(language)
    monkeypatch.setattr(app_policy, "discover", rows)
    path = tmp_path / "config.toml"
    path.write_text('# keep\n[options]\nlanguage="auto" # comment\n')
    app = SetupApp()
    async with app.run_test(size=(120, 36)):
        screen = await ready(app)
        assert not path.read_text().startswith("[apps]")
        assert screen.selected["work"]["WeChat"] == "freeze"
        await press(screen, "#setup-app-0", screen.changed)
        assert screen.selected["work"]["WeChat"] == "quit"
        screen.action_column("away")
        await press(screen, "#setup-app-0", screen.changed)
        assert screen.selected["away"]["WeChat"] == "quit"
        assert screen.query_one("#setup-save", Button).disabled
        await press(screen, "#setup-review", screen.reviewed)
        assert str(path) in str(screen.query_one("#setup-status", Static).content)
        assert not screen.query_one("#setup-save", Button).disabled
        await press(screen, "#setup-save", screen.completed)
    config = load_config(path)
    assert config.apps.work["WeChat"] == config.apps.away["WeChat"] == "quit"
    assert "# keep" in path.read_text() and "# comment" in path.read_text()


@pytest.mark.parametrize("dry_run", [False, True])
async def test_setup_skip_and_dry_run(tmp_path, monkeypatch, dry_run):
    monkeypatch.setattr(app_policy, "discover", rows)
    app = SetupApp(dry_run=dry_run)
    async with app.run_test(size=(80, 24)):
        screen = await ready(app)
        await press(screen, "#setup-skip", screen.completed)
        if dry_run:
            assert "setup_completed = true" in str(screen.query_one("#setup-status", Static).content)
    assert (tmp_path / "config.toml").exists() != dry_run
    if not dry_run:
        assert load_config().apps.setup_completed
        assert not load_config().apps.work and not load_config().apps.away


@pytest.mark.parametrize("stage", ["scan", "save"])
async def test_setup_failure_stays_visible_and_original_unchanged(tmp_path, monkeypatch, stage):
    from travel_mode.flows import setup

    path = tmp_path / "config.toml"
    original = "# keep\n[apps]\n"
    path.write_text(original)

    def broken(*args, **kwargs):
        raise OSError("injected setup error")

    monkeypatch.setattr(app_policy, "discover", broken if stage == "scan" else rows)
    if stage == "save":
        monkeypatch.setattr(setup, "write_app_choices", broken)
    app = SetupApp()
    async with app.run_test(size=(80, 24)):
        screen = await ready(app)
        if stage == "save":
            await press(screen, "#setup-review", screen.reviewed)
            await press(screen, "#setup-save", screen.completed)
        assert app.is_running
        text = str(screen.query_one("#setup-status", Static).content)
        assert "travel-mode.log" in text and "Traceback" not in text
        assert path.read_text() == original
        assert "injected setup error" in (tmp_path / "travel-mode.log").read_text()


async def test_first_run_setup_after_scenario_and_skip_not_repeated(tmp_path, monkeypatch):
    monkeypatch.setattr(app_policy, "discover", rows)
    app = TravelApp(first_run=True)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("w")
        await pilot._wait_for_screen()
        screen = await ready(app)
        assert not app.origin_ready
        await press(screen, "#setup-skip", screen.completed)
        await pilot._wait_for_screen()
        await app.wait_for_idle()
        assert app.origin_ready and app.refresh_complete
    assert load_config().apps.setup_completed


@pytest.mark.parametrize("accepted", [False, True])
async def test_tui_remember_once_and_background_updates_with_modal(tmp_path, monkeypatch, accepted):
    (tmp_path / "config.toml").write_text("")
    app = TravelApp(scenario="work")
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        app.offer_remember("Google Chrome", "freeze", "ask")
        await pilot._wait_for_screen()
        assert isinstance(app.screen, ConfirmRemember)
        # A modal must not divert the open-tool read workers away from the main UI.
        app.refresh_battery()
        await app.wait_for_idle()
        app.logline("ok", "fixture")
        app.screen.query_one("#remember-yes" if accepted else "#remember-no", Button).press()
        await pilot._wait_for_screen()
        assert not isinstance(app.screen, ConfirmRemember)
        app.offer_remember("Google Chrome", "freeze", "ask")
        assert not isinstance(app.screen, ConfirmRemember)
    assert load_config().apps.work == ({"Google Chrome": "freeze"} if accepted else {})


async def test_setup_space_cycles_once_and_review_is_invalidated(tmp_path, monkeypatch):
    monkeypatch.setattr(app_policy, "discover", rows)
    app = SetupApp()
    async with app.run_test(size=(80, 24)) as pilot:
        screen = await ready(app)
        screen.query_one("#setup-app-0", Button).focus()
        assert await screen.wait_for_refresh()
        screen.changed.clear()
        await pilot.press("space")
        await asyncio.wait_for(screen.changed.wait(), 10)
        assert screen.selected["work"]["WeChat"] == "quit"
        await press(screen, "#setup-review", screen.reviewed)
        screen.action_column("away")
        await press(screen, "#setup-app-0", screen.changed)
        assert screen.query_one("#setup-save", Button).disabled


@pytest.mark.parametrize("succeeds", [False, True])
async def test_apps_page_remembers_only_successful_changed_action(tmp_path, monkeypatch, succeeds):
    from travel_mode.system import apps
    from tui_interaction import click_after_layout

    (tmp_path / "config.toml").write_text("")
    monkeypatch.setattr(
        apps,
        "scan_apps",
        lambda: {
            "running": [dict(name="Google Chrome", path="/Fixture/Chrome.app", cpu="0.0", defact="ask")],
            "nobundle": [],
        },
    )
    monkeypatch.setattr(apps, "app_sleep_assertions", lambda _: [])
    monkeypatch.setattr(apps, "freeze_app", lambda _: succeeds)
    monkeypatch.setattr(apps, "freeze_result_count", lambda _: 2)
    app = TravelApp(scenario="work")
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        app.action_goto("p-apps")
        await click_after_layout(pilot, "#btn-0-f")
        assert isinstance(app.screen, ConfirmRemember) == succeeds
        if succeeds:
            app.screen.query_one("#remember-yes", Button).press()
            await pilot._wait_for_screen()
        assert app.st["session"]["actions"]["succeeded"] == int(succeeds)
        assert app.st["session"]["actions"]["failed"] == int(not succeeds)
    assert load_config().apps.work == ({"Google Chrome": "freeze"} if succeeds else {})


async def test_setup_review_shows_summary_before_enabling_save(monkeypatch):
    many = [dict(name=f"App {i:02}", work="freeze", away="freeze", reason="freeze") for i in range(30)]
    many[0]["name"] = "App [bold]literal[/]"
    monkeypatch.setattr(app_policy, "discover", lambda: many)
    app = SetupApp()
    async with app.run_test(size=(80, 24)):
        screen = await ready(app)
        assert "[bold]literal[/]" in str(screen.query_one("#setup-app-0", Button).label)
        await press(screen, "#setup-review", screen.reviewed)
        assert await screen.wait_for_refresh()
        status = screen.query_one("#setup-status", Static)
        assert screen.region.contains_region(status.region)
        assert status.region.bottom <= screen.query_one("#setup-controls").region.y
        assert not screen.query_one("#setup-save", Button).disabled


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 45)])
async def test_setup_compact_table_columns_buttons_and_focus(monkeypatch, language, size):
    from textual.containers import VerticalScroll

    from travel_mode.demo import setup_rows
    from travel_mode.i18n import t

    set_language(language)
    demo_rows = setup_rows()
    assert len(demo_rows) >= 25
    by_name = {row["name"]: row for row in demo_rows}
    assert not {"Terminal", "Safari", "TextEdit", "Calculator", "Books", "Photos", "Preview"} & by_name.keys()
    assert by_name["Box"]["reason"] == "provider"
    assert by_name["Box"]["work"] == by_name["Box"]["away"] == "quit"
    assert by_name["Nextcloud"]["reason"] == "freeze"
    assert {row["reason"] for row in demo_rows} == {"freeze", "provider", "assertion", "interactive"}
    monkeypatch.setattr(app_policy, "discover", lambda: demo_rows)
    app = SetupApp(dry_run=True)
    async with app.run_test(size=size) as pilot:
        screen = await ready(app)
        body = screen.query_one("#setup-body", VerticalScroll)
        buttons = list(screen.query(".setup-app"))
        assert all(button.region.height == 1 for button in buttons)
        assert all(button.label.cell_length <= button.content_region.width for button in buttons)
        assert all(group.region.height == 1 for group in screen.query(".setup-group"))
        visible = [button for button in buttons if body.content_region.contains_region(button.region)]
        if size == (120, 36):
            assert len(visible) >= 15
        for key in ("setup-column", "setup-review", "setup-skip", "setup-save"):
            button = screen.query_one(f"#{key}", Button)
            assert screen.region.contains_region(button.region)
            assert button.label.cell_length <= button.content_region.width - 2 * button.styles.line_pad
        header = str(screen.query_one("#setup-header", Static).content)
        assert t("scenario.work") in header and t("scenario.away") in header
        help_text = str(screen.query_one("#setup-help").content)
        assert t("scenario.work") in help_text and "work" not in help_text
        for scenario in ("work", "away"):
            screen.action_column(scenario)
            assert await screen.wait_for_refresh()
            # Inspect the label actually stored/rendered by Textual, not a recomputed row.
            label = buttons[0].label
            work_offset = label.plain.index(t("action.freeze"))
            away_offset = label.plain.index(t("action.quit"), work_offset + 1)
            work_style = label.get_style_at_offset(work_offset)
            away_style = label.get_style_at_offset(away_offset)
            active, inactive = (work_style, away_style) if scenario == "work" else (away_style, work_style)
            assert active.bold and active.underline
            assert inactive.dim and not inactive.underline
        screen.action_column("work")
        assert await screen.wait_for_refresh()
        # These changes share an input turn: the longer heading reflows at 80 columns.
        screen.action_column("away")
        screen.action_edge(True)
        await asyncio.wait_for(screen.focus_committed.wait(), 10)
        await pilot._wait_for_screen()
        assert await screen.wait_for_refresh()
        assert screen.focused is buttons[-1]
        assert body.content_region.contains_region(buttons[-1].region)
        await pilot.press("home")
        await asyncio.wait_for(screen.focus_committed.wait(), 10)
        assert await screen.wait_for_refresh()
        assert screen.focused is buttons[0]
        assert body.content_region.contains_region(buttons[0].region)
        await press(screen, "#setup-review", screen.reviewed)
        assert await screen.wait_for_refresh()
        save = screen.query_one("#setup-save", Button)
        assert not save.disabled
        assert not save.styles.text_style.reverse
        status = screen.query_one("#setup-status", Static)
        assert screen.region.contains_region(status.region)
        assert status.region.bottom <= screen.query_one("#setup-controls").region.y


async def test_setup_and_cli_omit_protected_rows_from_choices(monkeypatch, capsys):
    from types import SimpleNamespace

    from travel_mode.flows import setup
    from travel_mode.system import process_control
    from travel_mode.system.process_control import Process

    monkeypatch.setattr(process_control, "os", SimpleNamespace(getpid=lambda: 910011, getppid=lambda: 910010))
    fixture = [
        dict(
            name=name, path=f"/Fixture/{name}.app", bundle_id=bid, work="freeze", away="freeze", reason=reason
        )
        for name, bid, reason in (
            ("Terminal", "", "interactive"),
            ("Renamed terminal", "com.googlecode.iterm2", "interactive"),
            ("Ancestor", "org.fixture.ancestor", "freeze"),
            ("Foreground", "org.fixture.front", "foreground"),
            ("Ordinary", "org.fixture.ordinary", "freeze"),
        )
    ]
    processes = [
        Process(910010, 910009, "S", "fixture", "/fixture/parent"),
        Process(910009, 1, "S", "fixture", "/Fixture/Ancestor.app/Contents/Resources/Fixture"),
    ]
    monkeypatch.setattr(app_policy, "discover", lambda: app_policy.setup_candidates(fixture, processes))
    assert setup.run(yes=True, dry_run=True) == 0
    output = capsys.readouterr().out
    assert "Ordinary" in output
    assert all(name not in output for name in ("Terminal", "Renamed terminal", "Ancestor", "Foreground"))
    app = SetupApp(dry_run=True)
    async with app.run_test(size=(120, 36)):
        screen = await ready(app)
        assert [row["name"] for row in screen.rows] == ["Ordinary"]
        assert screen.selected == {"work": {"Ordinary": "freeze"}, "away": {"Ordinary": "freeze"}}
        assert len(screen.query(".setup-app")) == 1
        await press(screen, "#setup-app-0", screen.changed)
        assert screen.selected["work"] == {"Ordinary": "quit"}

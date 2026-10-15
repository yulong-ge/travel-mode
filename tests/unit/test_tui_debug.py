"""Safety and capture regression checks use fixtures and temporary paths only."""

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageFont

import travel_mode.flows.check as flow_check
from travel_mode.system import apps as system_apps
from travel_mode.system import launchd
from travel_mode.system.shell import ReadOnlyRunner

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def debug_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import terminal_render
    import tui_debug

    return terminal_render, tui_debug


@pytest.mark.parametrize(
    "command",
    [
        ["sudo", "launchctl", "list"],
        ["sudo", "-n", "true"],
        ["pmset", "-a", "sleep", "1"],
        ["launchctl", "unload", "example.plist"],
        ["blueutil", "-p", "0"],
        ["pkill", "-STOP", "Demo"],
        ["networksetup", "-setairportpower", "en0", "off"],
        ["tmutil", "stopbackup"],
    ],
)
def test_readonly_runner_rejects_before_execution(command, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Mutation reached subprocess")

    monkeypatch.setattr("travel_mode.system.shell.subprocess_runner", forbidden)
    with pytest.raises(PermissionError, match="refused command"):
        ReadOnlyRunner()(command)


def test_renderer_positions_punctuation_symbols_and_sgr(debug_modules, tmp_path, monkeypatch):
    renderer, _ = debug_modules
    # This checks terminal cells and backgrounds, not macOS glyph coverage.
    # Pillow embeds this font; no installed font or fallback is involved.
    fonts = {size: ImageFont.load_default(size=size) for size in (18, 19)}
    monkeypatch.setattr(renderer.ImageFont, "truetype", lambda font, size, **kwargs: fonts[size])
    text = "\x1b[31;44;1m中，→✓A\x1b[7mB\x1b[0m"
    cells = list(renderer.styled_cells(text))
    assert [cell[1] for cell in cells] == [0, 2, 4, 5, 6, 7]
    assert cells[0][4].bold and cells[-1][4].reverse
    for char in "，。：；（）、「」":
        assert renderer.font_family(char) == "cjk"
    path = tmp_path / "render.png"
    renderer.render(text, path, 10, 2)
    im = Image.open(path)
    assert im.getpixel((0, 23)) == renderer.PALETTE[4]
    assert im.getpixel((7 * renderer.CELL_W, 23)) == renderer.PALETTE[1]


def test_renderer_missing_system_font_is_an_error(debug_modules, tmp_path, monkeypatch):
    renderer, _ = debug_modules

    def missing_font(font, *args, **kwargs):
        raise OSError(f"cannot open resource: {font}")

    monkeypatch.setattr(renderer.ImageFont, "truetype", missing_font)
    path = tmp_path / "render.png"
    with pytest.raises(OSError, match=r"cannot open resource: /System/Library/Fonts/Menlo\.ttc"):
        renderer.render("text", path)
    assert not path.exists()


def test_lint_detects_real_defects_and_allows_command_names(debug_modules):
    _, debug = debug_modules
    text = "header\nOverview\n---\n---\n impl.fix Language 语言…\n" + "\ufffd" + "X" * 81
    issues = debug.lint_text(text, language="zh", cols=80, rows=24, tab="power")
    kinds = {issue["kind"] for issue in issues}
    assert {
        "replacement-character",
        "untranslated-key",
        "mixed-language-ui",
        "overflow",
        "truncated",
        "duplicate-language-hint",
    } <= kinds
    assert not debug.MESSAGE_KEY.search("com.example.service lowpowermode pmset -g ExampleApp")


@pytest.mark.parametrize("language", ["en", "zh"])
def test_blank_active_tab_lint_ignores_log_and_tab_titles(debug_modules, language):
    _, debug = debug_modules
    log = " Log " if language == "en" else " 日志 "
    text = "Header\nTabs\n\n\n\n│     │\n│     │\n" + log + "\ninvalid plist\n│ │\nFooter"
    layout = {"active": "p-svc", "body_visible": True, "content_visible": False}
    assert debug.log_has_text(text)
    assert not debug.body_has_text(text)
    assert debug.lint_active_tab(text, layout, observed=True)[0]["kind"] == "blank-active-tab"
    assert not debug.lint_active_tab(text, layout, observed=False)
    layout["content_visible"] = True
    assert not debug.lint_active_tab(text.replace("│     │", "│ Service │", 1), layout, observed=True)
    layout["body_visible"] = False
    assert debug.lint_active_tab(text, layout, observed=True)


@pytest.mark.parametrize("explicit", [True, False])
def test_config_is_copied_without_modifying_source(debug_modules, tmp_path, explicit):
    _, debug = debug_modules
    home = tmp_path / "host"
    source = home / ".config/travel-mode/config.toml"
    source.parent.mkdir(parents=True)
    source.write_text('[services]\nunload = ["com.example.agent"]\n')
    before = source.read_bytes(), source.stat().st_mtime_ns
    root = tmp_path / "isolated"
    root.mkdir()
    info = debug.copy_config(root, live=True, config=source if explicit else None, host_home=home)
    assert info["kind"] == ("explicit" if explicit else "user-copy")
    assert (root / "config.toml").read_bytes() == before[0]
    assert (root / "config.toml").stat().st_mode & 0o222 == 0
    (root / "config.toml").unlink()
    (root / "config.toml").write_text("isolated change")
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_compact_page_body_text_is_not_mistaken_for_blank_tab(debug_modules):
    _, debug = debug_modules
    text = "Header\nApps\n────\nDefault actions   Apply all\nDemo   Quit Freeze Skip\n\n Log \nmessage\n"
    layout = {"active": "p-apps", "body_visible": True, "content_visible": True}
    assert debug.body_has_text(text)
    assert not debug.lint_active_tab(text, layout, observed=True)
    blank = "Header\nApps\n────\n\n\n\n Log \nmessage\n"
    assert not debug.body_has_text(blank)
    assert debug.lint_active_tab(blank, layout, observed=True)[0]["kind"] == "blank-active-tab"


def test_missing_default_config_is_reported_but_explicit_missing_fails(debug_modules, tmp_path):
    _, debug = debug_modules
    info = debug.copy_config(tmp_path, live=True, config=None, host_home=tmp_path / "missing")
    assert info == {"kind": "built-in-default", "reason": "user config missing"}
    assert (tmp_path / "config.toml").read_text() == ""
    with pytest.raises(FileNotFoundError):
        debug.copy_config(tmp_path, live=True, config=tmp_path / "absent.toml", host_home=tmp_path)


def test_capture_wrapper_reuses_renderer(debug_modules):
    renderer, _ = debug_modules
    spec = importlib.util.spec_from_file_location("tmux_capture", SCRIPTS / "tmux_capture.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.render is renderer.render


def test_write_report_persists_lint_json_even_for_failed_runs(debug_modules, tmp_path):
    import json

    _, debug = debug_modules
    results = [{"screenshot": "x/dashboard.png", "issues": [{"kind": "overflow", "detail": "81 > 80"}]}]

    report = debug.write_report(
        tmp_path, results, "live-readonly", error="TimeoutError: TUI did not show '语言'"
    )

    data = json.loads((tmp_path / "lint.json").read_text(encoding="utf-8"))
    assert report is data or data["mode"] == "live-readonly"
    assert data["failure"].startswith("TimeoutError")
    assert data["issue_count"] == 2
    assert data["captures"][0]["issues"][0]["kind"] == "overflow"


def test_write_report_clean_run_has_no_failure(debug_modules, tmp_path):
    import json

    _, debug = debug_modules
    debug.write_report(tmp_path, [], "demo")

    data = json.loads((tmp_path / "lint.json").read_text(encoding="utf-8"))
    assert "failure" not in data
    assert data["issue_count"] == 0


async def test_debug_scroll_updates_widget_coordinates(debug_modules, tmp_path, monkeypatch):
    import json

    from textual.containers import VerticalScroll

    from travel_mode.paths import config_path

    _, debug = debug_modules
    config_path().write_text("")
    monkeypatch.setattr(system_apps, "APP_DIRS", [])
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": [], "kept": []})
    monkeypatch.setattr(
        flow_check,
        "blockers_lines",
        lambda: flow_check.BlockerLines([], [("warn", f"Demo warning {i}") for i in range(40)]),
    )
    telemetry = tmp_path / "layout.json"
    app = debug.DebugApp(telemetry)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        checkbox = app.query_one("#q-power")
        scroll = next(parent for parent in checkbox.ancestors if isinstance(parent, VerticalScroll))
        assert scroll.max_scroll_y > 0
        before = next(row for row in json.loads(telemetry.read_text())["widgets"] if row["id"] == "q-power")
        scroll.scroll_to(y=scroll.max_scroll_y, animate=False, immediate=True)
        await pilot.pause()
        after = next(row for row in json.loads(telemetry.read_text())["widgets"] if row["id"] == "q-power")
        assert after["region"][1] == checkbox.region.y
        assert after["region"][1] != before["region"][1]


@pytest.mark.parametrize(
    "page, control, panel", [("p-dash", "dash-audit", "dash-status"), ("p-net", "off-info", "net-box")]
)
async def test_debug_scrolled_page_bottom_is_not_blank(
    debug_modules, tmp_path, monkeypatch, page, control, panel
):
    import json

    from textual.containers import VerticalScroll

    from travel_mode.paths import config_path

    _, debug = debug_modules
    config_path().write_text("")
    monkeypatch.setattr(system_apps, "APP_DIRS", [])
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": [], "kept": []})
    telemetry = tmp_path / "layout.json"
    app = debug.DebugApp(telemetry)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        app.action_goto(page)
        await pilot.pause()
        if page == "p-dash":
            app.query_one(f"#{panel}").styles.height = 35
            await pilot.pause()
        target = app.query_one(f"#{control}")
        scroll = next(parent for parent in target.ancestors if isinstance(parent, VerticalScroll))
        scroll.scroll_to(y=scroll.max_scroll_y, animate=False, immediate=True)
        await pilot.pause()
        assert not app.visible_region(app.query_one(f"#{panel}")).area
        assert app.visible_region(target).area
        layout = json.loads(telemetry.read_text())
        assert layout["body_visible"] and layout["content_visible"]

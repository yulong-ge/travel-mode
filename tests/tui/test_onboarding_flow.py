"""Overview, expansion, recommendation, preview and save commit through explicit signals."""

import asyncio

import pytest
from textual.widgets import Button, Static

from travel_mode import onboarding
from travel_mode.config import load_config
from travel_mode.i18n import set_language, t
from travel_mode.tui.setup import SetupApp


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 45)])
async def test_new_setup_overview_expand_recommend_review_and_save(tmp_path, monkeypatch, language, size):
    set_language(language)
    data = dict(
        apps=[
            dict(
                name="Drawing App",
                group="other",
                reason="freeze",
                work="freeze",
                away="quit",
                configured=True,
            ),
            dict(name="VPN", group="network", reason="network", work="skip", away="skip"),
            dict(name="Awake", group="keep-awake", reason="assertion", work="freeze", away="quit"),
        ],
        services=[
            dict(
                label="org.example.backup",
                category="backup",
                group="sync-backup",
                recommendation="unload",
                configured=False,
            )
        ],
        readiness=dict(
            items=[
                dict(level="red", message=t("redesign.sleep_disabled")),
                dict(level="green", message=t("redesign.ready")),
            ]
        ),
    )
    monkeypatch.setattr(onboarding, "scan", lambda: data)
    path = tmp_path / "config.toml"
    path.write_text('# preserve\n[apps]\npolicy="adaptive"\n[apps.work]\nask=["Drawing App"]\n')
    app = SetupApp()
    async with app.run_test(size=size) as pilot:
        screen = app.screen
        await asyncio.wait_for(screen.ready.wait(), 10)
        assert await screen.wait_for_refresh()
        assert "Drawing App" not in str(screen.query_one("#setup-detected", Static).content)
        assert t("onboarding.group_vm") not in str(screen.query_one("#setup-detected", Static).content)
        assert not screen.query("#setup-group-vm") and not screen.query("#setup-group-sleep")
        assert len(screen.query("#setup-readiness Static")) == 1
        assert all(header.region.height == 0 for header in screen.query(".setup-header"))
        assert (
            screen.query_one("#setup-detected").content_region.x
            == screen.query_one("#setup-title").content_region.x
        )
        group = screen.query_one("#setup-group-other", Button)
        assert group.label.plain.startswith("▸ ")
        assert group.styles.content_align_horizontal == "left"
        assert group.styles.line_pad == 0
        name_offset = group.label.plain.index(t("onboarding.group_other"))
        assert group.label.get_style_at_offset(name_offset).bold
        assert screen.query_one("#setup-app-0").region.height == 0
        group.focus()
        await pilot.press("enter")
        await asyncio.wait_for(screen.expanded.wait(), 10)
        assert await screen.wait_for_refresh()
        assert screen.query_one("#setup-app-0").region.height == 1
        assert group.label.plain.startswith("▾ ")
        header = screen.query_one("#setup-header-other")
        assert header.region.height == 1 and header.region.bottom <= screen.query_one("#setup-app-0").region.y
        assert t("onboarding.your") in str(screen.query_one("#setup-app-0", Button).label)
        screen.changed.clear()
        screen.query_one("#setup-recommend", Button).press()
        await asyncio.wait_for(screen.changed.wait(), 10)
        assert screen.selected["work"]["Drawing App"] == "ask"
        screen.expanded.clear()
        screen.query_one("#setup-group-sync-backup", Button).press()
        await asyncio.wait_for(screen.expanded.wait(), 10)
        assert await screen.wait_for_refresh()
        from rich.cells import cell_len

        service = screen.query_one("#setup-service-0", Button)
        service_header = screen.query_one("#setup-header-sync-backup", Static).content
        positions = [
            i
            for i in range(len(service.label.plain))
            if service.label.plain.startswith(t("onboarding.unload"), i)
        ]
        assert len(positions) == 2
        for position, scenario in zip(positions, ("work", "away"), strict=True):
            assert cell_len(service.label.plain[:position]) == cell_len(
                service_header.plain[: service_header.plain.index(t("scenario." + scenario))]
            )
        assert service.label.plain.startswith("·  ") and service.region.height == 1
        assert t("onboarding.service_reason_backup") in service.label.plain
        for category, row_id in (("network", "setup-app-1"), ("keep-awake", "setup-app-2")):
            screen.expanded.clear()
            screen.query_one("#setup-group-" + category, Button).press()
            await asyncio.wait_for(screen.expanded.wait(), 10)
            assert await screen.wait_for_refresh()
            row = screen.query_one("#" + row_id, Button)
            assert row.label.plain.startswith("·  ") and row.region.height == 1
        screen.changed.clear()
        screen.query_one("#setup-service-0", Button).press()
        await asyncio.wait_for(screen.changed.wait(), 10)
        assert screen.selected["services"]["org.example.backup"] == "keep"
        assert service.label.plain.count(t("onboarding.keep")) == 2
        screen.query_one("#setup-review", Button).press()
        await asyncio.wait_for(screen.reviewed.wait(), 10)
        assert await screen.wait_for_refresh()
        assert onboarding.display_path(path) in str(screen.query_one("#setup-status", Static).content)
        assert screen.query_one("#setup-status").region.bottom < screen.query_one("#setup-controls").region.y
        assert not screen.query_one("#setup-save", Button).disabled
        screen.query_one("#setup-save", Button).press()
        await asyncio.wait_for(screen.completed.wait(), 10)
        assert await screen.wait_for_refresh()
        assert t("setup.saved") in str(screen.query_one("#setup-status", Static).content)
        assert screen.query_one("#setup-done").display
        assert all(
            screen.region.contains_region(screen.query_one(key).region)
            for key in ("#setup-detected", "#setup-status", "#setup-controls")
        )
    config = load_config()
    assert config.apps.work == {"Drawing App": "ask", "VPN": "skip", "Awake": "freeze"}
    assert config.apps.away == {"Drawing App": "quit", "VPN": "skip", "Awake": "quit"}
    assert config.services.keep == ("org.example.backup",) and not config.services.unload
    assert "# preserve" in path.read_text()


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 45)])
async def test_setup_normal_sleep_hidden_and_long_home_summary_fits(tmp_path, monkeypatch, language, size):
    from travel_mode.sleep_readiness import evaluate

    set_language(language)
    home = tmp_path / ("long-home-component-" * 6)
    path = home / ".config/travel-mode/config.toml"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRAVEL_MODE_CONFIG", str(path))
    monkeypatch.setattr(onboarding, "scan", lambda: dict(apps=[], services=[], readiness=evaluate("0")))
    app = SetupApp()
    async with app.run_test(size=size):
        screen = app.screen
        await asyncio.wait_for(screen.ready.wait(), 10)
        assert await screen.wait_for_refresh()
        assert not screen.query_one("#setup-readiness").display
        assert not screen.query(".setup-group") and not screen.query(".setup-header")
        assert str(screen.query_one("#setup-detected", Static).content) == t("onboarding.none")
        screen.query_one("#setup-review", Button).press()
        await asyncio.wait_for(screen.reviewed.wait(), 10)
        assert await screen.wait_for_refresh()
        status = screen.query_one("#setup-status", Static)
        assert "~/.config/travel-mode/config.toml" in str(status.content)
        assert str(home) not in str(status.content)
        assert status.max_scroll_y == 0
        assert screen.region.contains_region(status.region)
        assert status.region.bottom < screen.query_one("#setup-controls").region.y
        assert not path.exists()

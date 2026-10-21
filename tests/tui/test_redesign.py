"""The interface commits one list and one primary action per screen."""

import asyncio

import pytest
from textual.widgets import Checkbox, TabbedContent

from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout


@pytest.mark.parametrize("scenario", ["work", "away"])
async def test_plan_filters_preserve_selection_and_drawer_is_explicit(fake_runner, monkeypatch, scenario):
    from travel_mode.system import shell

    original = shell.command_path
    monkeypatch.setattr(
        shell, "command_path", lambda name: "/fixture/blueutil" if name == "blueutil" else original(name)
    )
    app = TravelApp(scenario=scenario, measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        assert app.refresh_complete
        assert not app.query(TabbedContent)
        assert not app.query_one("#details-drawer").display
        checkbox = app.query_one("#select-power-b-lowpowermode", Checkbox)
        checkbox.focus()
        assert await app.screen.wait_for_refresh()
        await pilot.press("space")
        assert checkbox.value is False
        selected = [(row["id"], row["selected"]) for row in app.plan_items]
        calls = list(fake_runner.calls)
        await click_after_layout(pilot, "#filter-network")
        assert [(row["id"], row["selected"]) for row in app.plan_items] == selected
        assert fake_runner.calls == calls
        for key in ("wifi", "bluetooth"):
            assert not app.query_one("#select-" + key, Checkbox).value
        await pilot.press("d")
        assert app.query_one("#details-drawer").display


@pytest.mark.parametrize("key,scenario", [("1", "work"), ("2", "away")])
async def test_scenario_cards_need_enter_to_confirm(key, scenario, fake_runner, monkeypatch):
    app = TravelApp(scenario="ask", measure=False)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("d")
        assert app.is_running and app._exception is None
        await app.action_toggle_language()
        await pilot._wait_for_screen()
        assert await app.screen.wait_for_refresh()
        assert app.focused.id == "scenario-work"
        await pilot.press(key)
        assert app.scenario == "ask"
        await pilot.press("enter")
        await app.wait_for_idle()
        assert app.scenario == scenario and app.refresh_complete
        await pilot._wait_for_screen()
        assert app.focused.id == "body"
        body = app.query_one("#body")
        scrolled = asyncio.Event()
        scroll_end = body.scroll_end

        def tracked_scroll_end(**kwargs):
            scroll_end(**kwargs, on_complete=scrolled.set)

        monkeypatch.setattr(body, "scroll_end", tracked_scroll_end)
        await pilot.press("end")
        await asyncio.wait_for(scrolled.wait(), 30)
        assert await app.screen.wait_for_refresh()
        assert body.scroll_y == body.max_scroll_y


@pytest.mark.parametrize(
    "state,running,controls",
    [
        ("planned", True, {"select", "cycle"}),
        ("running", True, set()),
        ("done", False, set()),
        ("warning", False, set()),
        ("failed", True, {"retry", "force"}),
        ("failed", False, set()),
        ("skipped", True, set()),
    ],
)
async def test_every_rendered_row_state_offers_only_permitted_controls(state, running, controls):
    from travel_mode.tui.plan import item

    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        row = item(
            "sample",
            "apps",
            "Sample App",
            "freeze",
            "A plain reason",
            dict(kind="app", path="/fixture/Sample.app", name="Sample App"),
        )
        row.update(state=state, app_running=running, result="A recorded result")
        app.plan_items = [row]
        await app.render_plan()
        rendered = app.query_one("#row-sample")
        actual = {w.id.split("-", 1)[0] for w in rendered.query("Button, Checkbox")}
        assert await app.screen.wait_for_refresh()
        for choice in rendered.query("ActionChoice"):
            assert choice.label.cell_length <= choice.content_region.width - 2 * choice.styles.line_pad
        assert actual == controls
        assert row["operations"] == list(
            filter(lambda op: op in controls, ["select", "cycle", "retry", "force"])
        )
        assert len([b for b in app.query("Button.-primary") if b.display]) == 1


async def test_noise_is_one_summary_drawer_preserves_technical_details_and_footer(fake_runner, monkeypatch):
    from travel_mode.i18n import t
    from travel_mode.system import apps, launchd

    monkeypatch.setattr(apps, "scan_apps", lambda: dict(running=[], nobundle=["Missing app selector"]))
    monkeypatch.setattr(
        launchd,
        "svc_scan",
        lambda: dict(
            unloadable=[],
            kept=[],
            invalid=[dict(plist_path="/fixture/broken.plist", error="not a plist")],
            unmatched=["org.fixture.missing"],
        ),
    )
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.wait_for_idle()
        assert app.query_one("#noise-summary").display
        assert not app.query_one("#details-drawer").display
        assert not any("broken.plist" in str(w.content) for w in app.query("#plan-list Static"))
        await click_after_layout(pilot, "#noise-summary")
        assert app.query_one("#details-drawer").display
        assert "broken.plist" in str(app.query_one("#row-evidence").content)
        app.logline("warn", "Fixture warning")
        assert "Fixture warning" in str(app.query_one("#last-result").content)
        assert str(app.query_one("#key-footer").content) == t("redesign.footer")
        await pilot.press("d")
        assert not app.query_one("#details-drawer").display


async def test_more_keeps_all_secondary_capabilities(fake_runner):
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.wait_for_idle()
        await click_after_layout(pilot, "#b-more")
        assert {b.id for b in app.screen.query("Button")} == {
            "b-new-plan",
            "b-setup",
            "b-measure",
            "b-energy",
            "b-history",
            "b-thaw",
            "b-automation",
            "b-category-sync",
            "b-category-updater",
            "close-more",
        }
        await pilot.press("escape")
        assert app.screen is app.screen_stack[0]


async def test_already_set_values_wrap_without_losing_names():
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(80, 24)):
        await app.wait_for_idle()
        app.plan_data["satisfied"]["power"] = ["Long setting name " + str(i) for i in range(20)]
        await app.render_plan()
        assert await app.screen.wait_for_refresh()
        notes = list(app.query(".satisfied"))
        assert len(notes) == 1 and notes[0].region.height > 1
        for index in range(20):
            assert "Long setting name " + str(index) in str(notes[0].content)


async def test_retired_plan_row_handles_already_queued_resize(fake_runner):
    from textual.events import Resize
    from textual.geometry import Size

    from travel_mode.tui.plan_row import PlanRow

    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        row = app.query_one(PlanRow)
        # Language recomposition removes children before the queued resize of
        # the old row is dispatched. Deliver that exact legal event ordering.
        await row.remove_children()
        row.on_resize(Resize(Size(116, 1), Size(116, 1)))
        assert app.is_running and app._exception is None

"""Review invariants use committed frames, with no timed waits or real mutations."""

import pytest
from textual.widgets import Static

from travel_mode.i18n import set_language, t
from travel_mode.tui.app import TravelApp
from travel_mode.tui.plan import item
from travel_mode.tui.view_models import plan_view


@pytest.mark.parametrize("kind", ["app", "power", "agent", "wifi", "bluetooth", "spotlight", "brightness"])
async def test_unselected_row_never_executes_or_appears_in_results(kind, monkeypatch, fake_runner):
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        row = item(
            "unchecked",
            "apps" if kind == "app" else "power",
            "Unchecked",
            "apply",
            "",
            dict(
                kind=kind,
                values=["lowpowermode", "1"],
                domain="-b",
                path="/fixture/Unchecked.app",
                name="Unchecked",
                value=0.5,
                device="en0",
            ),
            selected=False,
        )
        app.plan_items = [row]
        await app.render_plan()
        from travel_mode.tui import plan

        def forbidden(*args):
            pytest.fail("An unchecked row reached the Action factory")

        monkeypatch.setattr(plan, "operation", forbidden)
        commands = list(fake_runner.calls)
        await app.run_plan(app.query_one("#b-quick"))
        await app.wait_for_idle()
        assert not app.query("#row-unchecked")
        assert app.last_batch == dict(ok=0, failed=0)
        from travel_mode.system.shell import _dry_run_readonly

        assert all(_dry_run_readonly(list(cmd)) for cmd in fake_runner.calls[len(commands) :])


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_selected_health_coverage_reverses_without_rescanning(language, fake_runner):
    set_language(language)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()

        def health_text():
            return "\n".join(str(w.content) for w in app.query("#dash-health Static"))

        assert t("health_view.low_power_hit") not in health_text()
        checkbox = app.query_one("#select-power-b-lowpowermode")
        checkbox.focus()
        assert await app.screen.wait_for_refresh()
        calls = list(fake_runner.calls)
        await pilot.press("space")
        await app.wait_for_idle()
        assert t("health_view.low_power_hit") in health_text()
        assert fake_runner.calls == calls
        await pilot.press("space")
        await app.wait_for_idle()
        assert t("health_view.low_power_hit") not in health_text()
        row = next(r for r in app.plan_items if r["id"] == "power-b-lowpowermode")
        row.update(state="done", selected=False)
        app.view_mode = "session"
        await app.render_attention()
        assert t("health_view.low_power_hit") not in health_text()


async def test_footer_ignores_internal_events_and_drawer_does_not_repeat_result():
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        app.logline("ok", "Fixture action completed")
        app.logline("dim", "Internal refresh")
        assert "Fixture action completed" in str(app.query_one("#last-result").content)
        row = item("sample", "apps", "Demo", "quit", "", dict(kind="app"), evidence=["/Demo.app", "0.4"])
        row.update(
            result="Fixture action completed",
            detail_fields=dict(
                path="/Demo.app", cpu=0.4, command="osascript quit", returncode=1, apple_event=-128
            ),
        )
        app.plan_items = [row]
        app.focused_plan_id = "sample"
        app.update_details()
        evidence = str(app.query_one("#row-evidence", Static).content)
        assert "Fixture action completed" not in evidence
        assert "0.4%" in evidence and "-128" in evidence
        assert t("redesign.evidence_path", value="/Demo.app") in evidence


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("scenario", ["ask", "work", "away"])
async def test_default_footer_is_empty_on_every_screen_and_preserves_only_recent_result(language, scenario):
    from travel_mode import sessions

    set_language(language)
    app = TravelApp(scenario=scenario, measure=False)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        if scenario == "ask":
            assert not app.query("#last-result")
            assert app.last_result == ""
            return
        # An existing session's actions are not actions executed in this UI.
        sessions.begin(app.st)
        app.st["session"]["actions"]["succeeded"] = 3
        app.save()
        for mode in ("plan", "result", "session"):
            app.view_mode = mode
            await app.render_plan()
            app.update_session()
            assert str(app.query_one("#last-result").content) == ""
        await app.action_toggle_language()
        await app.wait_for_idle()
        assert str(app.query_one("#last-result").content) == ""
        app.logline("ok", "Fixture action completed")
        for mode in ("plan", "result", "session"):
            app.view_mode = mode
            await app.render_plan()
            app.logline("dim", "Internal refresh")
            assert "Fixture action completed" in str(app.query_one("#last-result").content)
        await app.action_toggle_language()
        await app.wait_for_idle()
        assert "Fixture action completed" in str(app.query_one("#last-result").content)


async def test_reapply_is_in_same_line_as_revived_message():
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        app.resurrected = [dict(kind="app", name="Demo", path="/Demo.app")]
        app.view_mode = "session"
        app.update_session()
        app.update_primary()
        assert await app.screen.wait_for_refresh()
        message, button = app.query_one("#session-revived"), app.query_one("#b-reapply")
        assert message.parent is button.parent
        assert message.region.y == button.region.y
        assert button.region.x >= message.region.right


@pytest.mark.parametrize("language", ["en", "zh"])
def test_power_target_actions_and_scoped_names(language):
    set_language(language)
    for key, value, label in [
        ("lowpowermode", "1", "target_on"),
        ("powernap", "0", "target_off"),
        ("sleep", "5", "target_minutes"),
    ]:
        row = item(key, "power", key, "apply", "", dict(kind="power", domain="-b", values=[key, value]))
        view = plan_view(row)
        assert view["action"] == t("redesign." + label, minutes=5)
    row = item("brightness", "power", "Brightness", "apply", "", dict(kind="brightness", value=0.5))
    assert plan_view(row)["action"] == t("health.action.brightness", percent=50)


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_short_screen_keeps_red_expanded_and_six_complete_plan_rows(language, fake_runner):
    from types import SimpleNamespace

    from travel_mode.sleep_readiness import evaluate
    from travel_mode.tui.plan_row import PlanRow

    set_language(language)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(80, 24)):
        await app.wait_for_idle()
        app.plan_data["readiness"] = evaluate("1", processes=[SimpleNamespace(command="UURemoteHelper")])
        app.plan_items = [
            item(
                str(n), "apps", "Fixture " + str(n), "freeze", "A reason", dict(kind="app", path="/Demo.app")
            )
            for n in range(25)
        ]
        app.plan_data["satisfied"]["power"] = []
        await app.render_observations()
        await app.render_plan()
        app.query_one("#body").scroll_home(animate=False, immediate=True)
        assert await app.screen.wait_for_refresh()
        body = app.query_one("#body").content_region
        visible = [w for w in app.query(PlanRow) if w.display and body.contains_region(w.region)]
        assert len(visible) >= 6
        assert app.query_one("#ready-0").region.y < visible[0].region.y
        assert app.query_one("#b-suggestions").display
        calls = list(fake_runner.calls)
        await app._handle_button_pressed(type("Event", (), {"button": app.query_one("#b-suggestions")})())
        assert fake_runner.calls == calls
        assert app.show_suggestions
        assert app.query_one("#ready-0").display


@pytest.mark.parametrize("scenario", ["work", "away"])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_scoped_satisfied_excludes_fixed_policy_and_unplanned_keys(scenario, language):
    from travel_mode.config import Config
    from travel_mode.system.pmset import POWER_KEYS, power_preview
    from travel_mode.tui.view_models import power_name

    set_language(language)
    profiles = dict(b={key: value for key, _, value in POWER_KEYS}, a=dict(powernap="0", womp="0"))
    data = power_preview(profiles, scenario=scenario, supported=set(profiles["b"]), config=Config.defaults())
    satisfied = data["satisfied_settings"]
    assert all(d["values"][0] != "tcpkeepalive" for d in satisfied)
    if scenario == "work":
        assert all(d["values"][0] in {"lowpowermode", "powernap", "womp"} for d in satisfied)
    assert {d["domain"] for d in satisfied if d["values"][0] == "powernap"} == {"-b", "-c"}
    for descriptor in satisfied:
        scope = t("redesign.scope_ac" if descriptor["domain"] == "-c" else "redesign.scope_battery")
        if descriptor["values"][0] in {"powernap", "womp"}:
            assert scope in power_name(descriptor)


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_default_plan_text_has_no_double_spaces_and_fixed_network_note(language):
    from travel_mode.tui.view_models import evidence_lines

    set_language(language)
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        text = []
        for row in app.plan_items:
            view = plan_view(row)
            text.extend([view["name"], view["reason"], view["action"], *evidence_lines(row)])
            if row["descriptor"]["kind"] == "power":
                assert view["action"] != t("redesign.apply_action")
                if language == "en" and view["reason"]:
                    assert view["reason"][0].isupper()
        text += [r["message"] for r in app.plan_data["readiness"]["items"]]
        text += [r["summary"] for r in app.plan_data["attention"]]
        assert all("  " not in line for line in text)
        assert str(app.query_one("#keepalive-note").content) == t("redesign.keepalive")
        assert not app.query("#select-keepalive-note")


async def test_filtered_debug_frame_commits_with_many_offscreen_rows(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from tui_debug import DebugApp

    app = DebugApp(tmp_path / "layout.json", scenario="work")
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        app.plan_items = [
            item(
                str(n), "apps", "Fixture " + str(n), "freeze", "A reason", dict(kind="app", path="/Demo.app")
            )
            for n in range(25)
        ]
        await app.render_plan()
        app.call_after_refresh(app.write_layout)
        await app.wait_layout(lambda frame: frame["ready"])
        app.set_filter("network")
        await app.wait_for_idle()
        assert await app.screen.wait_for_refresh()
        # Textual defers hidden subtrees until they are shown; they are not
        # part of the visible frame's completion barrier.
        assert all(
            not all(node.display for node in [w, *w.ancestors])
            for w in app.query("*")
            if w._layout_required or w._scroll_required
        )
        await app.wait_layout(lambda frame: frame["ready"] and frame["active_filter"] == "network")


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_assertion_holder_reason_matches_scenario(scenario, monkeypatch):
    from travel_mode.system import apps
    from travel_mode.tui.plan import collect

    monkeypatch.setattr(
        apps,
        "scan_apps",
        lambda: dict(
            running=[
                dict(
                    name="Remote Demo",
                    path="/Demo/Remote.app",
                    defact="freeze" if scenario == "work" else "quit",
                    reason="assertion",
                    cpu=0.4,
                )
            ],
            nobundle=[],
        ),
    )
    row = next(r for r in collect(scenario, {})["rows"] if r["descriptor"]["kind"] == "app")
    assert plan_view(row)["reason"] == t(
        "redesign.work_assertion" if scenario == "work" else "redesign.quit_reason"
    )


async def test_demo_results_use_same_selection_contract_and_labeled_evidence(
    tmp_path, monkeypatch, fake_runner
):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from tui_debug import DebugApp, write_report

    app = DebugApp(tmp_path / "layout.json", scenario="work")
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        app.plan_items = [
            item(str(n), "apps", name, "quit", "", dict(kind="app", path="/Demo/" + name + ".app"))
            for n, name in enumerate(["Google Drive", "Quit Refusal Demo"])
        ]
        app.plan_items.append(
            item(
                "unchecked",
                "power",
                "Brightness",
                "apply",
                "",
                dict(kind="brightness", value=0.5),
                selected=False,
            )
        )
        await app.render_plan()
        calls = list(fake_runner.calls)
        await app.action_demo_results()
        assert not app.query("#row-unchecked")
        assert app.last_batch == dict(ok=1, failed=1)
        assert fake_runner.calls == calls
        assert str(app.query_one("#row-evidence").content).count("Apple Event -128") == 1
        report = write_report(tmp_path, [], "demo")
        assert any(
            "no Action execution or selection changes" in step for step in report["operation_sequence"]
        )

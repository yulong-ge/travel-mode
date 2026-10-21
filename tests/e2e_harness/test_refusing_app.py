"""The new compiled fixture is source-bound and never executed on the host in tests."""

import pytest
from e2e_guard import GuardViolation
from e2e_local import product_environment
from e2e_refusal import SOURCE, compile_command, create
from e2e_ui import InteractiveHarness, answers, select_answer

from travel_mode import cli, resurrection, sessions, state
from travel_mode.i18n import set_language, t


def test_refusing_fixture_guard_binds_source_executable_and_bundle(sandbox):
    harness, guard, machine = sandbox
    create(harness.root, guard)
    assert ".terminateCancel" in (harness.root / "Refusing.swift").read_text()
    assert tuple(compile_command(harness.root)) in machine.calls
    (harness.root / "Refusing.swift").write_text(SOURCE + "// modified")
    with pytest.raises(GuardViolation):
        guard(compile_command(harness.root))
    with pytest.raises(GuardViolation):
        guard(["swiftc", "/tmp/other.swift", "-o", str(guard.refusing / "Contents/MacOS/refusing")])
    (harness.root / "Refusing.swift").unlink()
    with pytest.raises(GuardViolation):
        guard(compile_command(harness.root))


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_ui_harness_refusing_fixture_real_product_cli_path(sandbox, scenario, monkeypatch, capsys):
    local, guard, machine = sandbox
    if scenario == "work":
        machine.battery.update(powernap="0", womp="0", SleepServices="0")
    harness = InteractiveHarness(local.root, guard, local.real_state, pause=lambda _: None)
    harness.scenario = scenario
    try:
        with product_environment(harness.root, guard):
            set_language("en")
            harness.setup()
            assert harness.refusing_ready
            policy = answers(scenario)
            monkeypatch.setattr("builtins.input", lambda prompt: select_answer(prompt, policy)[1])
            assert cli.main(["--no-tui", "--no-measure", "--lang", "en"]) == (1 if scenario == "work" else 0)
            output = capsys.readouterr().out
            saved = state.load_state()
            counts = saved["session"]["actions"]
            summary = (
                t("failure.summary", n=counts["succeeded"], m=counts["failed"], names="").partition(
                    " Failed actions:"
                )[0]
                if counts["failed"]
                else t("failure.summary_success", n=counts["succeeded"])
            )
            assert summary in output
            if scenario == "work":
                assert counts == dict(attempted=8, succeeded=7, failed=1)
            assert not any(row["path"] == str(guard.refusing) for row in resurrection.detect(saved))
            harness.assert_on()
            assert cli.main(["off"]) == 0
            report = sessions.read_history()[-1]
            assert report["actions"] == counts
            assert not any(row["path"] == str(guard.refusing) for row in report["resurrected"])
            harness.assert_off()
            assert not guard.tripped
    finally:
        with product_environment(harness.root, guard):
            harness.cleanup()


async def test_tui_visible_plan_cli_summary_and_session_report_share_eight_units(sandbox, tmp_path):
    from e2e_ui import FixtureDebugApp

    from tui_interaction import click_after_layout
    from tui_plan import select_plan

    local, guard, machine = sandbox
    machine.battery.update(powernap="0", womp="0", SleepServices="0")
    harness = InteractiveHarness(local.root, guard, local.real_state, pause=lambda _: None)
    harness.scenario = "work"
    with product_environment(harness.root, guard):
        try:
            set_language("en")
            harness.setup()
            app = FixtureDebugApp(tmp_path / "layout.json", scenario="work")
            async with app.run_test(size=(120, 45)) as pilot:
                await app.wait_for_idle()
                ids = {
                    row["id"]
                    for row in app.plan_items
                    if row["descriptor"]["kind"] in {"power", "app", "agent"}
                    and row["action"] not in {"skip", "ask"}
                }
                assert len(ids) == 8
                await select_plan(app, ids=ids)
                await click_after_layout(pilot, "#b-quick")
                await app.wait_for_idle()
                counts = dict(attempted=8, succeeded=7, failed=1)
                assert app.last_batch == dict(ok=7, failed=1)
                assert app.st["session"]["actions"] == counts
                assert str(app.query_one("#result-summary").content).startswith(
                    t("redesign.result_summary", ok=7, failed=1)
                )
                assert not app.resurrected
                await click_after_layout(pilot, "#b-complete")
                await click_after_layout(pilot, "#b-off")
                report = sessions.read_history()[-1]
                assert report["actions"] == counts and report["resurrected"] == []
                assert t("report.actions", **counts) in str(app.screen.query_one("#session-text").content)
        finally:
            harness.cleanup()

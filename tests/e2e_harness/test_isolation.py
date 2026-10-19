"""Foreign apps remain observable to safety checks, outside product discovery."""

import plistlib
from dataclasses import replace
from pathlib import Path

import pytest
from e2e_fake import SimulatedBrightness
from e2e_guard import LABEL, PROCESS_QUERY
from e2e_local import create_fixtures, product_environment
from e2e_ui import InteractiveHarness, answers, select_answer

from conftest import FakeRunner
from travel_mode import cli, config, network_apps, resurrection, state
from travel_mode.categories import rules
from travel_mode.i18n import set_language
from travel_mode.system import apps, health, launchd, processes, shell
from travel_mode.system.shell import CmdResult

FOREIGN = {
    "OneDrive": "com.microsoft.OneDrive",
    "Google Drive": "com.google.drivefs",
    "Docker": "com.docker.docker",
    "Google Chrome": "com.google.Chrome",
    "Tailscale": "io.tailscale.ipn.macos",
    "Amphetamine": "com.if.Amphetamine",
    "Background Worker": "org.example.background-worker",
}


@pytest.fixture
def dirty_machine(sandbox, tmp_path, monkeypatch):
    harness, guard, machine = sandbox
    foreign = tmp_path / "foreign-apps"
    foreign.mkdir()
    for i, (name, bid) in enumerate(FOREIGN.items(), 701):
        bundle = foreign / (name + ".app")
        (bundle / "Contents/MacOS").mkdir(parents=True)
        (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bid}))
        machine.rows[i] = ("S", "Thu Oct 15 12:00:00 2026", str(bundle / "Contents/MacOS" / name))
    machine.rows[799] = ("S", "Thu Oct 15 12:00:00 2026", "/usr/bin/caffeinate")
    extra = FakeRunner(
        {
            ("mdfind", 'kMDItemContentType == "com.apple.application-bundle"'): CmdResult(
                "\n".join(str(p) for p in foreign.glob("*.app")), "", 0
            ),
            ("/usr/bin/pmset", "-g", "assertions"): CmdResult(
                "Assertion status system-wide:\n"
                ' pid 799(caffeinate): PreventUserIdleSystemSleep named: "caffeinate"\n'
                ' pid 701(OneDrive): PreventSystemSleep named: "sync"\n',
                "",
                0,
            ),
            ("ps", "-ww", "-axo", "pid=,command="): CmdResult("799 /usr/bin/caffeinate -d", "", 0),
            ("launchctl", "list"): CmdResult("- 0 com.google.GoogleUpdater.wake\n", "", 0),
        }
    )

    def backend(command, timeout=None, interactive=False):
        base = (
            machine(command, timeout, interactive)
            if command != ["ps", "-ww", "-axo", "pid=,command="]
            else CmdResult("", "", 0)
        )
        key = tuple(command)
        if command == health.PROCESS_COMMAND:
            rows = [line.split(None, 10) for line in base.stdout.splitlines()]
            for row in rows:
                if 701 <= int(row[0]) <= 707:
                    row[4] = "80.0"
            return replace(base, stdout="\n".join(" ".join(row) for row in rows))
        if key in extra.responses:
            result = extra(command, timeout, interactive)
            return replace(base, stdout="\n".join(part for part in [base.stdout, result.stdout] if part))
        return base

    guard.backend = backend
    monkeypatch.setattr(apps, "APP_DIRS", [str(foreign)])
    return harness, guard, machine


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_demo_cli_driver_never_prompts_or_plans_foreign_apps(dirty_machine, scenario, monkeypatch, capsys):
    original, guard, machine = dirty_machine
    harness = InteractiveHarness(
        original.root, guard, original.real_state, pause=lambda _: None, brightness_loader=SimulatedBrightness
    )
    harness.scenario = scenario
    set_language("en")
    with product_environment(harness.root, guard), harness.brightness_capability():
        try:
            harness.setup()
            policy = answers(scenario)
            prompts = []

            def answer(prompt):
                prompts.append(prompt)
                return select_answer(prompt, policy)[1]

            monkeypatch.setattr("builtins.input", answer)
            expected = 1 if scenario == "work" and harness.refusing_ready else 0
            assert cli.main(["--no-tui", "--lang", "en"]) == expected
            assert all(name not in prompt for prompt in prompts for name in FOREIGN)
            assert all(Path(row["path"]).is_relative_to(harness.root) for row in apps.scan_apps()["running"])
            assert not network_apps.configured_names()
            assert not processes.caffeinate_procs()
            assert all(r["label"] == LABEL for r in launchd.svc_scan()["recognized"])
            assert set(machine.rows).issuperset({701, 702, 703, 704, 705, 706, 799})
            assert all(machine.rows[p][0] == "S" for p in [701, 702, 703, 704, 705, 706, 799])
            harness.assert_on()
            assert cli.main(["off"]) == 0
            harness.assert_off()
        finally:
            harness.cleanup()
    assert not guard.tripped
    # Full process safety snapshots must still see the foreign processes.
    assert all(str(pid) in guard(PROCESS_QUERY).stdout for pid in [701, 702, 703, 799])


def test_local_config_explicitly_disables_builtin_categories_and_defaults(dirty_machine):
    harness, guard, _ = dirty_machine
    before_dirs = apps.APP_DIRS
    before_runner = shell.get_runner()
    with product_environment(harness.root, guard):
        create_fixtures(harness.root)
        cfg = config.load_config()
        assert cfg.categories.pause == ()
        assert set(cfg.categories.ignore) == {r["id"] for r in rules()}
        assert not cfg.network.apps and not cfg.apps.with_daemon
        assert set(cfg.apps.all) == {"TravelModeE2E-Quit", "TravelModeE2E-Freeze"}
        assert resurrection.detect(state.new_state()) == []
    assert apps.APP_DIRS is before_dirs and shell.get_runner() is before_runner


@pytest.mark.parametrize("scenario", ["work", "away"])
async def test_tui_fixture_plan_excludes_foreign_apps_and_health_controls(dirty_machine, scenario, tmp_path):
    from e2e_ui import FixtureDebugApp
    from textual.widgets import Button

    original, guard, _ = dirty_machine
    harness = InteractiveHarness(
        original.root, guard, original.real_state, pause=lambda _: None, brightness_loader=SimulatedBrightness
    )
    set_language("en")
    with product_environment(harness.root, guard), harness.brightness_capability():
        try:
            harness.setup()
            app = FixtureDebugApp(tmp_path / "layout.json", scenario=scenario)
            async with app.run_test(size=(120, 45)):
                await app.wait_for_idle()
                assert app.refresh_complete
                assert not any(
                    getattr(b, "health_descriptor", {}).get("kind") == "caffeinate" for b in app.query(Button)
                )
                assert all(Path(row["path"]).is_relative_to(harness.root) for row in app.scan["running"])
                assert all(
                    Path(row["descriptor"]["path"]).is_relative_to(harness.root)
                    for row in app.plan_items
                    if row["descriptor"]["kind"] == "app"
                )
                assert not any(hasattr(b, "health_descriptor") for b in app.query("#dash-health Button"))
                text = "\n".join(str(w.content) for w in app.query("#dash-health Static"))
                assert all(name not in text for name in FOREIGN)
                assert all(name not in str(b.label) for name in FOREIGN for b in app.query(Button))
        finally:
            harness.cleanup()


def test_scoped_discovery_preserves_read_errors(dirty_machine):
    from e2e_isolation import INDEX_QUERY, FixtureDiscoveryRunner

    harness, guard, _ = dirty_machine
    fake = FakeRunner({tuple(INDEX_QUERY): CmdResult("", "fixture index denied", 1)})
    guard.backend = fake
    result = FixtureDiscoveryRunner(guard, harness.root)(INDEX_QUERY, timeout=5)
    assert result.rc == 1 and result.stderr == "fixture index denied"
    assert fake.calls == [tuple(INDEX_QUERY)]


def test_assertion_projection_does_not_hide_identity_read_failure(dirty_machine):
    from e2e_isolation import FixtureDiscoveryRunner

    from travel_mode.system.shell import ReadError

    harness, _, _ = dirty_machine
    command = ["pmset", "-g", "assertions"]
    fake = FakeRunner(
        {
            tuple(command): CmdResult("pid 799(caffeinate): PreventSystemSleep", "", 0),
            tuple(PROCESS_QUERY): CmdResult("", "fixture identity denied", 1),
        }
    )
    with pytest.raises(ReadError, match="fixture identity denied"):
        FixtureDiscoveryRunner(fake, harness.root)(command, timeout=2)

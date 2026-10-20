"""Battery harness changes only stateful fake fixtures, never host power settings."""

from datetime import datetime, timedelta, timezone

import pytest
from e2e_battery import (
    QUICK_SELECTION,
    assert_battery_interval,
    assert_comparison,
    assert_report,
    evidence,
    require_battery,
)
from e2e_fake import BatteryMachine, InstantClock
from e2e_guard import GuardRunner, GuardViolation
from e2e_isolation import write_fixture_config
from e2e_local import create_fixtures, fixture_paths, product_environment
from e2e_ui import InteractiveHarness, answers, main, select_answer

from conftest import FakeRunner
from travel_mode import cli, config, measurement, sessions
from travel_mode.i18n import set_language, t
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


@pytest.fixture
def battery_sandbox(tmp_path, darwin_platform, monkeypatch):
    root = tmp_path / "travel-mode-e2e-battery"
    root.mkdir()
    machine = BatteryMachine(root)
    guard = GuardRunner(
        machine, root, fixture_paths(root, battery_measure=True), root / "agent.plist", battery_measure=True
    )
    harness = InteractiveHarness(root, guard, tmp_path / "unused-state", pause=lambda _: None)
    harness.scenario, harness.battery_measure = "work", True
    clock = InstantClock()
    monkeypatch.setattr(measurement, "Clock", lambda: clock)
    monkeypatch.setattr(measurement, "CancellableClock", lambda _: clock)
    baseline = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(sessions, "utc_now", lambda: (baseline + timedelta(seconds=clock.now)).isoformat())
    with product_environment(root, guard), harness.brightness_capability():
        try:
            yield harness, guard, machine
        finally:
            harness.cleanup()


def test_ac_refused_before_fixture_startup(tmp_path):
    runner = FakeRunner({("pmset", "-g", "ps"): CmdResult("AC Power", "", 0)})
    shell.set_runner(runner)
    with pytest.raises(RuntimeError, match="requires battery power"):
        require_battery()
    assert runner.calls == [("pmset", "-g", "ps")]


@pytest.mark.parametrize(
    "arguments",
    [
        ["sudo", "pmset", "-b", "lowpowermode", "1"],
        ["sudo", "/usr/bin/pmset", "-c", "powernap", "0", "womp", "0"],
        ["sudo", "-n", "/usr/bin/pmset", "-a", "disablesleep", "0"],
        ["sudo", "-n", "-l", "/usr/bin/pmset", "-b", "lowpowermode", "1"],
        ["/usr/bin/sudo", "-n", "/usr/bin/pmset", "-b", "sleep", "1"],
        ["pmset", "-b", "lowpowermode", "1"],
        ["/usr/bin/pmset", "displaysleepnow"],
    ],
)
def test_battery_guard_refuses_power_writes_before_backend(tmp_path, arguments):
    backend = FakeRunner()
    guard = GuardRunner(
        backend,
        tmp_path,
        fixture_paths(tmp_path, battery_measure=True),
        tmp_path / "agent.plist",
        battery_measure=True,
    )
    with pytest.raises(GuardViolation):
        guard(arguments)
    assert backend.calls == []


@pytest.mark.parametrize("language", ["en", "zh"])
def test_battery_cli_policy_says_no_to_every_power_prompt(language):
    set_language(language)
    policy = answers("work", battery_measure=True)
    for prompt, (key, answer) in policy.items():
        if key.startswith("battery-") or key in {
            "impl.fix_pmset_disablesleep",
            "impl.use_hibernatemode_write_ram_to_disk",
            "impl.reduce_ac_activity_too_disable_powernap",
            "impl.reduce_brightness_to_off_restores_the",
            "impl.turn_off_the_display_now_pmset",
        }:
            assert answer == "n"
            if isinstance(prompt, str):
                assert select_answer(prompt, policy)[1] == "n"
    assert policy[t("scenario.input")][1] == "work"


def comparison(saving):
    result = {
        "before": {"available": True, "average_watts": 20, "capacity_source": "BatteryData"},
        "after": {"available": True, "average_watts": 20 - saving, "capacity_source": "BatteryData"},
        "saved_watts": saving,
        "extra_hours": 1,
    }
    for phase in ("before", "after"):
        result[phase].update(
            power_source="PowerTelemetryData.SystemLoad",
            samples=3,
            interval_count=2,
            intervals=[{"average_watts": result[phase]["average_watts"]}] * 2,
        )
    return result


@pytest.mark.parametrize("saving", [-1, 0, 0.99, float("nan")])
def test_threshold_failure_prints_both_windows_and_samples(battery_sandbox, capsys, saving):
    _, guard, _ = battery_sandbox
    with pytest.raises(AssertionError, match=r">= 1\.0 W"):
        assert_comparison(guard, comparison(saving))
    output = capsys.readouterr().out
    assert '"before"' in output and '"after"' in output and '"samples"' in output


def test_threshold_boundary_and_external_power_invalidates_run(battery_sandbox):
    _, guard, machine = battery_sandbox
    assert assert_comparison(guard, comparison(1))["minimum_saving_watts"] == 1
    machine.external = True
    guard(["ioreg", "-rn", "AppleSmartBattery"])
    with pytest.raises(AssertionError, match="external power"):
        assert_comparison(guard, comparison(10))


@pytest.mark.parametrize("status", ["unknown", "external_seen"])
def test_session_with_ac_or_unknown_coverage_is_invalid(status):
    with pytest.raises(AssertionError, match="Invalid battery run"):
        assert_report({"power_status": status})
    output = (
        "2026-01-01 10:00:00 Using Batt\n2026-01-01 10:10:00 Using AC\n" if status == "external_seen" else ""
    )
    shell.set_runner(FakeRunner({("pmset", "-g", "log"): CmdResult(output, "", 0)}))
    from travel_mode.sessions import timestamp

    with pytest.raises(AssertionError, match="full-run power coverage"):
        assert_battery_interval(
            timestamp("2026-01-01T10:00:00+00:00"), timestamp("2026-01-01T11:00:00+00:00")
        )


def test_short_battery_run_without_power_change_events_is_valid(fake_runner):
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult("", "", 0)
    observed = dict(external=False, charging=False)
    assert (
        assert_battery_interval(10, 190, start_observation=observed, end_observation=observed)
        == "battery_only"
    )


def test_battery_run_ac_change_is_invalid_despite_battery_endpoints(fake_runner):
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult(
        "2026-01-01 10:01:00 +0000 Using AC\n2026-01-01 10:02:00 +0000 Using Batt", "", 0
    )
    observed = dict(external=False, charging=False)
    with pytest.raises(AssertionError, match="full-run power coverage=external_seen"):
        assert_battery_interval(
            sessions.timestamp("2026-01-01T10:00:00+00:00"),
            sessions.timestamp("2026-01-01T10:03:00+00:00"),
            start_observation=observed,
            end_observation=observed,
        )


def test_busy_fixture_has_two_private_interpreters_and_no_sleep(tmp_path):
    create_fixtures(tmp_path, battery_measure=True)
    for directory in ("MacOS", "Helpers"):
        path = tmp_path / "TravelModeE2E-Burn.app/Contents" / directory
        assert (path / "sh").is_file()
        assert (path / "loop").read_text().startswith(f"#!{path}/sh\n")
        assert "while :; do :; done" in (path / "loop").read_text()
        assert "sleep" not in (path / "loop").read_text()


def without_log_events(monkeypatch, machine):
    original = type(machine).__call__

    def invoke(self, command, timeout=None, interactive=False):
        if command == ["/usr/bin/pmset", "-g", "log"]:
            return CmdResult("", "", 0)
        return original(self, command, timeout, interactive)

    monkeypatch.setattr(type(machine), "__call__", invoke)


@pytest.mark.parametrize("empty_log", [False, True])
def test_battery_cli_round_trip_measures_only_fixture_freeze(battery_sandbox, monkeypatch, empty_log):
    harness, guard, machine = battery_sandbox
    if empty_log:
        without_log_events(monkeypatch, machine)
    set_language("en")
    original = machine.power_text()
    harness.setup()
    policy = answers("work", battery_measure=True)
    monkeypatch.setattr("builtins.input", lambda prompt: select_answer(prompt, policy)[1])
    assert cli.main(["--no-tui", "--lang", "en"]) == 0
    harness.assert_on()
    assert cli.main(["off", "--lang", "en"]) == 0
    harness.assert_off()
    assert machine.power_text() == original and not guard.tripped
    assert harness.session_report["measurement"]["saved_watts"] == pytest.approx(15.6)
    assert all(
        row["capacity_source"] == "BatteryData" for row in evidence(guard, harness.comparison)["samples"]
    )
    assert not any(c[0] == "sudo" for c in guard.commands)


def test_battery_plan_contains_read_only_power_commands_and_quick_selection(capsys):
    assert main(["--battery-measure", "--plan"]) == 0
    output = capsys.readouterr().out
    assert "/usr/bin/pmset -g ps" in output and "ioreg -rn AppleSmartBattery" in output
    assert "saving >= 1.0 W" in output and "while :; do :; done" in output
    assert "240 s timeout" in output and "up to 48 polls" in output
    assert "sudo -n" not in output and "pmset -b" not in output
    assert '"power": false' in output
    assert QUICK_SELECTION == {"apps": True, "services": True, "power": False, "network": False}


@pytest.mark.parametrize("empty_log", [False, True])
async def test_battery_tui_round_trip_uses_measured_fixture_only_quick_action(
    battery_sandbox, monkeypatch, empty_log
):
    from travel_mode.tui.app import TravelApp
    from tui_interaction import click_after_layout

    harness, guard, machine = battery_sandbox
    if empty_log:
        without_log_events(monkeypatch, machine)
    set_language("en")
    harness.setup()
    original = machine.power_text()
    app = TravelApp(scenario="work")
    async with app.run_test(size=(160, 55)) as pilot:
        await app.wait_for_idle()
        from e2e_battery import select_fixture_plan

        select_fixture_plan(app, measure=True)
        await app.render_plan()
        assert not any(r["selected"] for r in app.plan_items if r["group"] == "power")
        await click_after_layout(pilot, "#b-quick")
        await app.wait_for_idle()
        harness.assert_on()
        await click_after_layout(pilot, "#b-complete")
        await click_after_layout(pilot, "#b-off")
        await app.wait_for_idle()
        harness.assert_off()
        await pilot.press("escape")
        await app.wait_for_idle()
    assert machine.power_text() == original and not guard.tripped


def test_battery_config_keeps_two_interval_policy_and_ui_timeout(tmp_path, monkeypatch):
    from e2e_ui import UISession

    monkeypatch.setenv("TRAVEL_MODE_CONFIG", str(tmp_path / "config.toml"))
    for battery_mode in (False, True):
        write_fixture_config(tmp_path, battery_measure=battery_mode)
        settings = config.load_config().measurement
        assert settings.seconds == (240 if battery_mode else 180)
        assert settings.updates == (3 if battery_mode else 2)
    # Construct the driver without starting tmux or a product subprocess.
    monkeypatch.setattr("e2e_ui.shutil.which", lambda _: "/fixture/bin/uv")
    monkeypatch.setattr(UISession, "tmux", lambda *args: "")
    session = UISession(tmp_path, tmp_path, "cli", True, "work", battery_measure=True)
    assert session.timeout == 600
    assert 2 * settings.seconds + settings.settle_seconds < session.timeout

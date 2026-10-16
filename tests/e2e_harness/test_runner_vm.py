"""Observed VM capabilities skip individual phases without changing GuardRunner."""

from pathlib import Path

import pytest
import yaml
from e2e_fake import Machine
from e2e_guard import GuardRunner
from e2e_local import fixture_paths, product_environment
from e2e_ui import InteractiveHarness, answers, select_answer

from travel_mode import cli
from travel_mode.i18n import set_language


class VirtualMachine(Machine):
    def __init__(self, root):
        super().__init__(root)
        self.battery = {}
        self.tcc = "Not authorized to send Apple events (-1743)"

    def power_text(self):
        return "AC Power:\n" + "".join(f" {key} {value}\n" for key, value in self.ac.items())


class NoBacklight:
    def get(self):
        raise RuntimeError("DisplayServices Get returned 1000; no adjustable display")


@pytest.fixture
def vm(tmp_path, darwin_platform):
    root = tmp_path / "travel-mode-e2e-vm"
    root.mkdir()
    machine = VirtualMachine(root)
    guard = GuardRunner(machine, root, fixture_paths(root), root / "agent.plist")
    harness = InteractiveHarness(
        root, guard, tmp_path / "unused-state", pause=lambda _: None, brightness_loader=NoBacklight
    )
    try:
        with product_environment(root, guard), harness.brightness_capability():
            yield harness, guard, machine
    finally:
        with product_environment(root, guard):
            harness.cleanup()


def test_vm_local_fixture_passes_power_and_native_brightness_skip(vm, capsys):
    harness, guard, _machine = vm
    # Local sandbox still exercises force quit, exact freeze, agent unload and off.
    harness.sandbox()
    harness.power()
    harness.brightness()
    assert not guard.tripped
    assert harness.skips[-1]["stage"] == "power"
    assert harness.brightness_report["status"] == "SKIP"
    assert not any(cmd[0] == "sudo" for cmd in guard.commands)
    output = capsys.readouterr().out
    assert "PASS: Same freeze" in output and "No Battery Power profile" in output


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_vm_cli_round_trip_skips_power_and_tcc_but_tests_fixture_actions(vm, monkeypatch, scenario):
    harness, guard, machine = vm
    harness.scenario = scenario
    set_language("en")
    original = machine.power_text()
    harness.setup()
    assert harness.fixture_only_power and not harness.graceful_ready
    policy = answers(scenario, battery_measure=harness.fixture_only_power)
    for step, answer in policy.values():
        if step.startswith("battery-"):
            assert answer == "n"
    monkeypatch.setattr("builtins.input", lambda prompt: select_answer(prompt, policy)[1])
    assert cli.main(["--no-tui", "--lang", "en"]) == 0
    harness.assert_on()
    assert cli.main(["off", "--lang", "en"]) == 0
    harness.assert_off()
    assert machine.power_text() == original and not guard.tripped
    assert not any(cmd[0] == "sudo" for cmd in guard.commands)


@pytest.mark.parametrize("scenario", ["work", "away"])
async def test_vm_tui_fixture_only_round_trip_waits_for_actions(vm, scenario):
    from e2e_battery import QUICK_SELECTION
    from textual.widgets import Checkbox

    from travel_mode.tui.app import TravelApp
    from tui_interaction import click_after_layout

    harness, guard, machine = vm
    harness.scenario = scenario
    set_language("en")
    harness.setup()
    original = machine.power_text()
    app = TravelApp(scenario=scenario)
    async with app.run_test(size=(160, 55)) as pilot:
        await app.wait_for_idle()
        for name, value in {**QUICK_SELECTION, "q-measure": False}.items():
            app.query_one("#" + name, Checkbox).value = value
        await app.wait_for_idle()
        await click_after_layout(pilot, "#b-quick")
        await app.wait_for_idle()
        harness.assert_on()
        await pilot.press("5")
        await app.wait_for_idle()
        await click_after_layout(pilot, "#b-off")
        await app.wait_for_idle()
        harness.assert_off()
        await pilot.press("escape")
        await app.wait_for_idle()
    assert machine.power_text() == original and not guard.tripped
    assert not any(cmd[0] == "sudo" for cmd in guard.commands)


def test_hosted_sudo_permission_does_not_expand_guard(sandbox):
    harness, guard, _machine = sandbox
    harness.run()
    assert any(command[:3] == ("sudo", "-n", "-l") for command in guard.commands)
    assert not guard.tripped
    from e2e_guard import GuardViolation

    with pytest.raises(GuardViolation):
        guard(["sudo", "-n", "/usr/bin/pmset", "-c", "sleep", "0"])


def test_power_prerequisite_refuses_battery_before_permission_probe(sandbox, monkeypatch):
    from travel_mode.system import pmset

    harness, guard, _machine = sandbox
    monkeypatch.setattr(pmset, "on_ac_power", lambda: False)
    with pytest.raises(AssertionError, match="AC connected"):
        harness.power()
    assert not any(command[0] == "sudo" for command in guard.commands)


def test_macos_workflow_is_separate_bounded_and_keeps_failure_artifacts():
    raw = Path(".github/workflows/e2e-macos.yml").read_text()
    workflow = yaml.safe_load(raw)
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch", "schedule", "push"}
    assert triggers["push"]["branches"] == ["main"]
    job = workflow["jobs"]["e2e"]
    assert job["runs-on"] == "macos-latest" and job["timeout-minutes"] == 30
    upload = job["steps"][-1]
    assert upload["if"] == "always()" and upload["with"]["retention-days"] == 14
    assert "scripts/e2e_local.py" in raw and "scripts/e2e_ui.py" in raw
    assert "sudo " not in raw and "tccutil" not in raw and "continue-on-error" not in raw

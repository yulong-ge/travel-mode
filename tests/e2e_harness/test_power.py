"""Profile invariants and failure evidence use FakeRunner only."""

import pytest

from conftest import FakeRunner
from e2e_harness.conftest import e2e
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


def profile_text(profiles):
    return "".join(
        name + ":\n" + "".join(f" {key} {value}\n" for key, value in profile.items())
        for name, profile in profiles.items()
    )


@pytest.fixture
def power_snapshot(tmp_path):
    before = {
        name: {"powernap": "1", "SleepServices": "1", "womp": "1", "sleep": "25", "displaysleep": "10"}
        for name in ("AC Power", "Battery Power")
    }
    runner = FakeRunner()
    harness = e2e.Harness(tmp_path, runner, tmp_path / "unused-state")
    harness.original_power = profile_text(before)
    harness.original_sleep = "1"

    def observe(after, sleep="0"):
        runner.responses = {
            ("pmset", "-g", "custom"): CmdResult(profile_text(after), "", 0),
            ("pmset", "-g"): CmdResult(f"SleepDisabled {sleep}\n", "", 0),
        }
        shell.set_runner(runner)

    return harness, before, observe


@pytest.mark.parametrize("domain", ["AC Power", "Battery Power"])
@pytest.mark.parametrize("original,target", [("1", "0"), ("0", "1")])
@pytest.mark.parametrize("derived_changes", [True, False])
def test_sleepservices_follows_changed_powernap(power_snapshot, domain, original, target, derived_changes):
    harness, before, observe = power_snapshot
    before[domain].update(powernap=original, SleepServices=original)
    harness.original_power = profile_text(before)
    after = {name: dict(profile) for name, profile in before.items()}
    after[domain]["powernap"] = target
    if derived_changes:
        after[domain]["SleepServices"] = target
    observe(after)
    harness.check_profile(before[domain], after[domain], {"powernap": target}, domain)


@pytest.mark.parametrize("domain", ["AC Power", "Battery Power"])
@pytest.mark.parametrize("selected", [True, False])
def test_sleepservices_change_requires_changed_powernap(power_snapshot, domain, selected, capsys):
    harness, before, observe = power_snapshot
    after = {name: dict(profile) for name, profile in before.items()}
    after[domain]["SleepServices"] = "0"
    other = "Battery Power" if domain == "AC Power" else "AC Power"
    after[other].update(powernap="0", SleepServices="0")
    observe(after)
    targets = {"powernap": "1"} if selected else {"womp": "1"}
    with pytest.raises(AssertionError, match="unselected keys unchanged"):
        harness.check_profile(before[domain], after[domain], targets, domain)
    output = capsys.readouterr().out
    assert "--- before/pmset-custom" in output and "+++ after/pmset-custom" in output
    assert "@@" in output and "- SleepServices 1" in output and "+ SleepServices 0" in output
    assert "SleepDisabled: before='1', after='0'" in output


@pytest.mark.parametrize("domain", ["AC Power", "Battery Power"])
def test_sleepservices_opposite_powernap_transition_rejected(power_snapshot, domain):
    harness, before, observe = power_snapshot
    before[domain]["SleepServices"] = "0"
    harness.original_power = profile_text(before)
    after = {name: dict(profile) for name, profile in before.items()}
    after[domain].update(powernap="0", SleepServices="1")
    observe(after)
    with pytest.raises(AssertionError, match="unselected keys unchanged"):
        harness.check_profile(before[domain], after[domain], {"powernap": "0"}, domain)


@pytest.mark.parametrize("domain", ["AC Power", "Battery Power"])
@pytest.mark.parametrize("key", ["sleep", "displaysleep", "womp"])
def test_other_unselected_power_keys_rejected(power_snapshot, domain, key, capsys):
    harness, before, observe = power_snapshot
    after = {name: dict(profile) for name, profile in before.items()}
    after[domain].update(powernap="0", SleepServices="0")
    after[domain][key] = "0"
    observe(after)
    with pytest.raises(AssertionError, match="unselected keys unchanged"):
        harness.check_profile(before[domain], after[domain], {"powernap": "0"}, domain)
    output = capsys.readouterr().out
    assert f"- {key} {before[domain][key]}" in output and f"+ {key} 0" in output


@pytest.mark.parametrize("add_key", [True, False])
def test_unselected_power_key_addition_or_removal_rejected(power_snapshot, add_key):
    harness, before, observe = power_snapshot
    after = {name: dict(profile) for name, profile in before.items()}
    if add_key:
        after["AC Power"]["unexpected"] = "1"
    else:
        del after["AC Power"]["SleepServices"]
    observe(after)
    with pytest.raises(AssertionError, match="unselected keys unchanged"):
        harness.check_profile(before["AC Power"], after["AC Power"], {"powernap": "1"}, "AC")


def test_selected_power_target_failure_prints_diff(power_snapshot, capsys):
    harness, before, observe = power_snapshot
    after = {name: dict(profile) for name, profile in before.items()}
    after["AC Power"]["womp"] = "0"
    observe(after)
    with pytest.raises(AssertionError, match="selected power targets reached"):
        harness.check_profile(before["AC Power"], after["AC Power"], {"powernap": "0"}, "AC")
    output = capsys.readouterr().out
    assert "--- before/pmset-custom" in output and "+ womp 0" in output
    assert "SleepDisabled: before='1', after='0'" in output


@pytest.mark.parametrize("change", ["derived", "whitespace", "sleepdisabled"])
def test_power_off_requires_exact_snapshot_and_prints_diagnostics(power_snapshot, change, capsys):
    harness, before, observe = power_snapshot
    after = {name: dict(profile) for name, profile in before.items()}
    if change == "derived":
        after["AC Power"]["SleepServices"] = "0"
    observe(after, sleep="0" if change == "sleepdisabled" else "1")
    if change == "whitespace":
        harness.runner.responses[("pmset", "-g", "custom")] = CmdResult(profile_text(after) + "\n", "", 0)
    with pytest.raises(AssertionError, match=r"SleepDisabled restored|byte for byte"):
        harness.verify_power()
    output = capsys.readouterr().out
    assert "pmset -g custom unified diff (before/after):" in output
    if change != "sleepdisabled":
        assert "--- before/pmset-custom" in output and "+++ after/pmset-custom" in output
    assert f"SleepDisabled: before='1', after={'0' if change == 'sleepdisabled' else '1'!r}" in output


def test_powernap_sleepservices_full_fake_roundtrip(sandbox):
    harness, guard, machine = sandbox
    original = machine.power_text()
    stages = []

    def check(stage):
        if stage == "power-applied":
            assert machine.ac["powernap"] == machine.ac["SleepServices"] == "0"
            assert machine.battery["powernap"] == machine.battery["SleepServices"] == "0"
            stages.append(stage)

    harness.checkpoint = check
    harness.run()
    assert stages == ["power-applied"]
    assert machine.power_text() == original
    assert not guard.tripped

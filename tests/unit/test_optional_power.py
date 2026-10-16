"""Recorded AC-only custom output; -g is SYNTHETIC (trace retained byte count only)."""

import json
from pathlib import Path

import pytest

from travel_mode import snapshots
from travel_mode.system import pmset
from travel_mode.system.shell import CmdResult, ReadError

FIXTURES = Path(__file__).parents[1] / "fixtures/hosted-vm"


def install_vm_outputs(fake_runner):
    fake_runner.responses[("pmset", "-g")] = CmdResult(
        (FIXTURES / "pmset-g-synthetic.txt").read_text(), "", 0
    )
    fake_runner.responses[("pmset", "-g", "custom")] = CmdResult(
        (FIXTURES / "pmset-custom-recorded.txt").read_text(), "", 0
    )


def test_missing_sleep_switch_does_not_abort_snapshot_or_invent_original(fake_runner):
    install_vm_outputs(fake_runner)
    assert pmset.sleep_disabled() is None
    snapshot, merged = snapshots.snapshot_origin()
    assert not merged
    assert "sleepdisabled" not in snapshot["power_origin"]
    assert "batt_prof" not in snapshot["power_origin"]
    assert snapshot["power_origin"]["ac_prof"]


@pytest.mark.parametrize(
    "raw",
    [
        "SleepDisabled\n",
        "SleepDisabled xx\n",
        "SleepDisabled 2\n",
        "SleepDisabled 0 garbage\n",
        "SleepDisabled 0\nSleepDisabled 1\n",
    ],
)
def test_present_invalid_sleep_switch_is_still_read_error(fake_runner, raw):
    fake_runner.responses[("pmset", "-g")] = CmdResult(raw, "", 0)
    with pytest.raises(ReadError, match="SleepDisabled"):
        pmset.sleep_disabled()


def test_ac_keys_do_not_authorize_battery_changes(fake_runner):
    install_vm_outputs(fake_runner)
    assert pmset.power_recommendations() == []


def test_low_power_alias_uses_battery_capability_not_ac_keys(fake_runner):
    fake_runner.responses[("pmset", "-g", "custom")] = CmdResult(
        "Battery Power:\n powermode 0\nAC Power:\n lowpowermode 0\n", "", 0
    )
    assert pmset.power_recommendations()[0][::2] == ("powermode", "1")


@pytest.mark.parametrize("missing", ["a", "b"])
def test_absent_profile_has_no_snapshot_original(fake_runner, missing):
    remaining = "Battery Power" if missing == "a" else "AC Power"
    fake_runner.responses[("pmset", "-g", "custom")] = CmdResult(f"{remaining}:\n sleep 10\n", "", 0)
    snapshot, _ = snapshots.snapshot_origin()
    absent = "ac_prof" if missing == "a" else "batt_prof"
    assert absent not in snapshot["power_origin"]
    assert pmset.pmget("sleep", missing) is None


@pytest.mark.parametrize("raw", ["sleep", "sleep xx", "sleep -1", "sleep 1 extra", "sleep 1\n sleep 2"])
def test_malformed_advertised_profile_key_is_read_error(raw):
    with pytest.raises(ReadError, match="sleep"):
        pmset.parse_custom("Battery Power:\n " + raw + "\n")


@pytest.mark.parametrize("raw", ["", "broken format"])
def test_invalid_pmset_output_is_not_an_absent_capability(raw):
    with pytest.raises(ReadError):
        pmset.parse_sleep_disabled(raw)


def test_unavailable_sleep_switch_in_check_and_json(fake_runner, capsys):
    from jsonschema import validate

    from travel_mode.flows.check import mode_check
    from travel_mode.i18n import t
    from travel_mode.structured import check_data

    install_vm_outputs(fake_runner)
    assert mode_check() == 0
    output = capsys.readouterr().out
    assert t("health.unavailable") in output and t("power.sleep_switch_reason") in output
    result = check_data()
    assert "audit_error" not in result
    sleep = next(row for row in result["health"]["checks"] if row["id"] == "sleep_disabled")
    assert sleep["status"] == "unavailable" and sleep["actions"] == [] and sleep["error"] is None
    assert sleep["evidence"] == [t("power.sleep_switch_reason")]
    assert all(not row["supported"] and row["current"] is None for row in result["power"])
    validate(result, json.loads(Path("docs/json-schema/check.schema.json").read_text()))


def test_missing_low_power_field_is_unavailable_without_repair(fake_runner):
    from travel_mode.system.health import collect

    install_vm_outputs(fake_runner)
    result = next(row for row in collect()["checks"] if row["id"] == "low_power")
    assert result["status"] == "unavailable" and result["actions"] == []


def test_recorded_empty_ioreg_is_explicitly_unavailable():
    from travel_mode.system.battery import parse

    assert parse((FIXTURES / "ioreg-empty-recorded.txt").read_text()) == {
        "available": False,
        "reason": "no_battery",
    }


def test_absent_optional_ioreg_time_estimate_does_not_block_power():
    from travel_mode.system.battery import parse

    text = (FIXTURES.parent / "battery/discharging.txt").read_text().replace('"TimeRemaining" = 240\n', "")
    result = parse(text)
    assert result["available"] and result["watts"] == 12
    assert result["ioreg_minutes"] is None


@pytest.mark.parametrize("raw", ["", "unknown power source"])
def test_invalid_power_source_never_implies_battery(fake_runner, raw):
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult(raw, "", 0)
    with pytest.raises(ReadError, match="pmset -g ps"):
        pmset.on_ac_power()


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_absent_switch_and_ac_profile_keep_battery_round_trip(scenario, fake_runner, darwin_platform):
    from travel_mode import cli, state

    fake_runner.responses[("pmset", "-g")] = CmdResult("Currently in use:\n lowpowermode 0\n", "", 0)
    fake_runner.responses[("pmset", "-g", "custom")] = CmdResult(
        "Battery Power:\n lowpowermode 0\n sleep 10\n", "", 0
    )
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    assert cli.main(["--no-tui", "--scenario", scenario, "--no-measure", "--yes"]) == 0
    snapshot = state.load_state()
    assert snapshot is not None and snapshot["batt_prof"]
    assert "sleepdisabled" not in snapshot["power_origin"]
    assert "ac_prof" not in snapshot["power_origin"]
    assert cli.main(["off"]) == 0
    assert state.load_state() is None
    writes = [call for call in fake_runner.calls if call[:2] == ("sudo", "pmset")]
    assert writes and all(call[2] == "-b" for call in writes)
    assert "disablesleep" not in {part for call in writes for part in call}

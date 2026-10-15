"""Battery fixtures are generic public fields, never host observations."""

import json
import threading
from pathlib import Path

import pytest
from jsonschema import validate

from travel_mode import cli, measurement, state
from travel_mode.config import ConfigError, load_config
from travel_mode.system import battery
from travel_mode.system.shell import CmdResult

FIXTURES = Path(__file__).parents[1] / "fixtures/battery"


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.parametrize("name", ["discharging", "unsigned"])
def test_discharge_signed_current_and_capacity_math(name):
    reading = battery.parse((FIXTURES / f"{name}.txt").read_text())
    assert reading["current_ma"] == -1000
    assert reading["watts"] == 12
    assert reading["remaining_wh"] == 48
    assert reading["remaining_hours"] == 4
    assert reading["ioreg_minutes"] == 240


@pytest.mark.parametrize(
    "name,reason", [("charging", "external_power"), ("ac", "external_power"), ("no-battery", "no_battery")]
)
def test_unmeasurable_has_reason_without_synthetic_numbers(name, reason):
    assert battery.parse((FIXTURES / f"{name}.txt").read_text()) == {"available": False, "reason": reason}


@pytest.mark.parametrize(
    "field,value",
    [
        ("Voltage", "oops"),
        ("IsCharging", "maybe"),
        ("AppleRawMaxCapacity", "0"),
        ("InstantAmperage", str(2**64)),
        ("TimeRemaining", "-1"),
    ],
)
def test_invalid_fields_have_command_and_key_context(field, value):
    text = (FIXTURES / "discharging.txt").read_text()
    import re

    text = re.sub(rf'"{field}" = [^\n]+', f'"{field}" = {value}', text)
    with pytest.raises(battery.BatteryError, match=field):
        battery.parse(text)


def test_missing_fields_and_empty_registry_are_distinct():
    with pytest.raises(battery.BatteryError, match="Voltage"):
        battery.parse((FIXTURES / "missing.txt").read_text())
    with pytest.raises(battery.BatteryError, match="ExternalConnected"):
        battery.parse("malformed non-empty output")


def test_amperage_alternative_and_unknown_time():
    text = (
        (FIXTURES / "discharging.txt")
        .read_text()
        .replace("InstantAmperage", "Amperage")
        .replace('"TimeRemaining" = 240', '"TimeRemaining" = 65535')
    )
    assert battery.parse(text)["ioreg_minutes"] is None
    assert battery.parse(text)["watts"] == 12


def test_finite_window_uses_ten_reads_and_fake_time(fake_runner):
    fake_runner.responses[tuple(battery.COMMAND)] = CmdResult((FIXTURES / "unsigned.txt").read_text(), "", 0)
    fake_runner.responses[("pmset", "-g", "batt")] = CmdResult("80%; discharging; 3:45 remaining", "", 0)
    clock = FakeClock()
    result = measurement.measure(clock=clock)
    assert clock.now == 20
    assert result["samples"] == 10
    assert result["average_watts"] == 12
    assert result["remaining_hours"] == 4
    assert result["macos"]["minutes"] == 225
    assert fake_runner.calls.count(tuple(battery.COMMAND)) == 10
    assert fake_runner.calls.count(("pmset", "-g", "batt")) == 1
    validate(result, json.loads(Path("docs/json-schema/power.schema.json").read_text()))


def test_window_stops_when_power_source_changes(monkeypatch):
    rows = iter(
        [battery.parse((FIXTURES / "discharging.txt").read_text()), battery.unavailable("external_power")]
    )
    monkeypatch.setattr(battery, "read", lambda: next(rows))
    clock = FakeClock()
    result = measurement.measure(clock=clock)
    assert result["reason"] == "external_power"
    assert "average_watts" not in result
    assert clock.now == 2


def test_comparison_uses_same_remaining_charge_and_survives_state_reload(monkeypatch, tmp_paths):
    reading = battery.parse((FIXTURES / "discharging.txt").read_text())
    rows = iter([reading, {**reading, "watts": 6, "remaining_wh": 42}])
    monkeypatch.setattr(battery, "read", lambda: next(rows))
    clock = FakeClock()
    settings = measurement.Settings(seconds=2, interval=2, settle_seconds=10)
    output = []
    before = measurement.begin("work", None, settings, output.append, clock=clock)
    snapshot = state.new_state()
    result = measurement.finish(before, settings, snapshot, state.save_state, output.append, clock=clock)
    assert clock.now == 14
    assert result["saved_watts"] == 6
    assert result["extra_hours"] == 3.5
    assert state.load_state()["measurement"] == result


@pytest.mark.parametrize(
    "scenario,enabled,expected", [("work", None, 1), ("work", False, 0), ("away", None, 0), ("away", True, 1)]
)
def test_apply_measurement_policy_and_no_wait_on_ac(scenario, enabled, expected, fake_runner):
    clock = FakeClock()
    messages = []
    assert measurement.begin(scenario, enabled, measurement.Settings(), messages.append, clock=clock) is None
    assert fake_runner.calls.count(tuple(battery.COMMAND)) == expected
    assert clock.now == 0


def test_cancelled_window_does_not_sample_or_sleep(fake_runner):
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        measurement.measure(clock=FakeClock(), cancelled=event)
    assert not fake_runner.calls


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_window_rejected_before_sampling(value, fake_runner):
    with pytest.raises(ValueError):
        measurement.measure(measurement.Settings(seconds=value), clock=FakeClock())
    assert not fake_runner.calls


def test_invalid_measurement_config_and_state_are_explicit(tmp_paths):
    config = tmp_paths / "config.toml"
    config.write_text("[measurement]\ninterval=0.1\n")
    with pytest.raises(ConfigError, match="measurement"):
        load_config()
    path = tmp_paths / "state.json"
    path.write_text('{"measurement": {}}')
    with pytest.raises(state.StateError, match="measurement"):
        state.load_state()


def test_power_command_json_and_unavailable_source(darwin_platform, capsys):
    assert cli.main(["power", "--seconds", "20", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "external_power"
    validate(result, json.loads(Path("docs/json-schema/power.schema.json").read_text()))


def test_cli_work_comparison_runs_around_actions_without_real_sleep(
    tmp_paths, darwin_platform, fake_runner, monkeypatch, capsys
):
    (tmp_paths / "config.toml").write_text("[measurement]\nseconds=2\ninterval=2\nsettle_seconds=10\n")
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    reading = battery.parse((FIXTURES / "discharging.txt").read_text())
    observed = []

    def read():
        changed = any(call[:3] == ("sudo", "pmset", "-b") for call in fake_runner.calls)
        observed.append(changed)
        return {**reading, "watts": 6 if changed else 12}

    monkeypatch.setattr(battery, "read", read)
    clock = FakeClock()
    monkeypatch.setattr(measurement, "Clock", lambda: clock)
    assert cli.main(["--scenario", "work", "--yes"]) == 0
    assert observed == [False, True]
    assert clock.now == 14
    assert state.load_state()["measurement"]["saved_watts"] == 6
    assert "12.00" in capsys.readouterr().out


def test_negative_saving_is_reported_without_clamping(monkeypatch):
    reading = battery.parse((FIXTURES / "discharging.txt").read_text())
    monkeypatch.setattr(battery, "read", lambda: {**reading, "watts": 24})
    before = measurement.measure(measurement.Settings(seconds=2), clock=FakeClock(), first=reading)
    result = measurement.finish(
        before,
        measurement.Settings(seconds=2, settle_seconds=0),
        {},
        lambda value: None,
        lambda message: None,
        clock=FakeClock(),
    )
    assert result["saved_watts"] == -12
    assert result["extra_hours"] == -2

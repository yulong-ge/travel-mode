"""Capacity schema regression from an allowlisted, deidentified ioreg recording."""

import json
from pathlib import Path

import pytest
from jsonschema import validate
from test_battery import FakeClock, fresh_readings

from battery_support import properties, registry
from travel_mode import cli, measurement, sessions
from travel_mode.system import battery
from travel_mode.system.shell import CmdResult, ReadError

FIXTURE = Path(__file__).parents[1] / "fixtures/battery/battery-data.txt"


def test_recorded_battery_data_capacity_and_unsigned_current():
    reading = battery.parse(FIXTURE.read_text())
    assert reading["capacity_source"] == "BatteryData"
    assert reading["current_ma"] == -1481
    assert reading["remaining_mah"] == 4781
    assert reading["maximum_mah"] == 4820
    assert reading["watts"] == pytest.approx(12339 * 1481 / 1_000_000)
    assert reading["remaining_wh"] == pytest.approx(12339 * 4781 / 1_000_000)
    assert reading["remaining_hours"] == pytest.approx(4781 / 1481)
    assert reading["ioreg_minutes"] == 199


def test_top_level_capacity_layout_takes_precedence():
    data = properties(FIXTURE.read_text())
    data.update(AppleRawCurrentCapacity=4000, AppleRawMaxCapacity=5000)
    reading = battery.parse(registry(data))
    assert reading["capacity_source"] == "AppleRaw"
    assert reading["remaining_mah"] == 4000
    assert reading["maximum_mah"] == 5000


@pytest.mark.parametrize(
    "case,key",
    [
        ("missing", "BatteryData.RemainingCapacity"),
        ("zero", "BatteryData.FullChargeCapacity"),
        ("invalid", "BatteryData.RemainingCapacity"),
        ("partial", "AppleRawMaxCapacity"),
        ("absent", "capacity"),
        ("nested", "BatteryData.RemainingCapacity"),
    ],
)
def test_invalid_selected_schema_does_not_substitute_other_capacity(case, key):
    data = properties(FIXTURE.read_text())
    capacity = data["BatteryData"]
    if case == "missing":
        del capacity["RemainingCapacity"]
    elif case == "zero":
        capacity["FullChargeCapacity"] = 0
    elif case == "invalid":
        capacity["RemainingCapacity"] = "invalid"
    elif case == "partial":
        data["AppleRawCurrentCapacity"] = 4000
    elif case == "absent":
        data["OtherData"] = data.pop("BatteryData")
    elif case == "nested":
        capacity["Nested"] = {"RemainingCapacity": capacity.pop("RemainingCapacity")}
    with pytest.raises(battery.BatteryError, match=key):
        battery.parse(registry(data))


def test_measurement_and_session_use_recorded_capacity(fake_runner, monkeypatch):
    fake_runner.responses[tuple(battery.COMMAND)] = CmdResult(FIXTURE.read_text(), "", 0)
    fake_runner.responses[("pmset", "-g", "batt")] = CmdResult("79%; discharging; 3:19 remaining", "", 0)
    fresh_readings(monkeypatch)
    result = measurement.measure(measurement.Settings(seconds=20), clock=FakeClock())
    assert result["average_watts"] == pytest.approx(12339 * 1481 / 1_000_000)
    assert result["capacity_source"] == "BatteryData"
    assert "BatteryData" in measurement.describe(result)
    assert "BatteryData" in battery.describe(battery.read())
    validate(result, json.loads(Path("docs/json-schema/power.schema.json").read_text()))
    endpoint = sessions.observe()
    assert endpoint["remaining_mah"] == 4781
    assert endpoint["errors"] == []


@pytest.mark.parametrize("error_type", [battery.BatteryError, ReadError])
@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_power_read_failure_is_friendly_and_json_is_structured(
    darwin_platform, monkeypatch, capsys, error_type, as_json, language
):
    def fail(*args, **kwargs):
        raise error_type("ioreg: invalid capacity\nrecording")

    monkeypatch.setattr(measurement, "measure", fail)
    assert cli.main(["power", "--lang", language, *(["--json"] if as_json else [])]) == 4
    output = capsys.readouterr()
    assert "Traceback" not in output.err
    if as_json:
        result = json.loads(output.out)
        assert result["error"]["message"] == "ioreg: invalid capacity recording"
        assert result["error"]["type"] == "BatteryError"
        assert "average_watts" not in result
        validate(result, json.loads(Path("docs/json-schema/power.schema.json").read_text()))
    else:
        assert not output.out
        assert "ioreg: invalid capacity recording" in output.err
        assert len(output.err.splitlines()) == 1


@pytest.mark.parametrize("as_json", [False, True])
def test_power_debug_preserves_measurement_exception_chain(darwin_platform, monkeypatch, as_json):
    monkeypatch.setenv("TRAVEL_MODE_DEBUG", "1")

    def fail(*args, **kwargs):
        raise ReadError("ioreg: timeout")

    monkeypatch.setattr(measurement, "measure", fail)
    with pytest.raises(battery.BatteryError) as caught:
        cli.main(["power", *(["--json"] if as_json else [])])
    assert isinstance(caught.value.__cause__, ReadError)

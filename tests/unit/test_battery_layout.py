"""Capacity schema regression from an allowlisted, deidentified ioreg recording."""

import json
from pathlib import Path

import pytest
from jsonschema import validate
from test_battery import FakeClock, fresh_readings

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
    text = (
        FIXTURE.read_text().rsplit("}", 1)[0]
        + '\n"AppleRawCurrentCapacity" = 4000\n"AppleRawMaxCapacity" = 5000\n}\n'
    )
    reading = battery.parse(text)
    assert reading["capacity_source"] == "AppleRaw"
    assert reading["remaining_mah"] == 4000
    assert reading["maximum_mah"] == 5000


@pytest.mark.parametrize(
    "change,key",
    [
        (lambda s: s.replace('"RemainingCapacity"=4781,', ""), "BatteryData.RemainingCapacity"),
        (
            lambda s: s.replace('"FullChargeCapacity"=4820', '"FullChargeCapacity"=0'),
            "BatteryData.FullChargeCapacity",
        ),
        (
            lambda s: s.replace('"RemainingCapacity"=4781', '"RemainingCapacity"="invalid"'),
            "BatteryData.RemainingCapacity",
        ),
        (lambda s: s.rsplit("}", 1)[0] + '\n"AppleRawCurrentCapacity" = 4000\n}\n', "AppleRawMaxCapacity"),
        (lambda s: s.replace('"BatteryData"', '"OtherData"'), "capacity"),
        (
            lambda s: s.replace('"RemainingCapacity"=4781', '"Nested"={"RemainingCapacity"=4781}'),
            "BatteryData.RemainingCapacity",
        ),
    ],
)
def test_invalid_selected_schema_does_not_substitute_other_capacity(change, key):
    with pytest.raises(battery.BatteryError, match=key):
        battery.parse(change(FIXTURE.read_text()))


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

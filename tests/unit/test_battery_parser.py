"""Keep session endpoints and power windows on one structural registry parser."""

from pathlib import Path

import pytest

from battery_support import FakeClock, properties, registry
from travel_mode import measurement, sessions
from travel_mode.system import battery
from travel_mode.system.shell import CmdResult

FIXTURE = Path(__file__).parents[1] / "fixtures/battery/nested-charger.txt"


def test_recorded_nested_charger_cannot_shadow_session_status(fake_runner):
    fake_runner.responses[tuple(battery.COMMAND)] = CmdResult(FIXTURE.read_text(), "", 0)
    fake_runner.responses[("pmset", "-g", "batt")] = CmdResult("79%; discharging; 3:19 remaining", "", 0)
    endpoint = sessions.observe()
    assert endpoint["errors"] == []
    assert endpoint["charging"] is False and endpoint["external"] is False
    assert endpoint["remaining_mah"] == 4781 and endpoint["voltage_mv"] == 12339
    assert endpoint["telemetry"]["system_count"] == 122874


def test_multiline_nested_status_and_child_registry_cannot_shadow_battery():
    data = properties(FIXTURE.read_text())
    data["ChargerData"] = {
        "IsCharging": True,
        "Voltage": 1,
        "Nested": [{"ExternalConnected": True, "RemainingCapacity": 1}],
    }
    data["IORegistryEntryChildren"] = [{"IsCharging": True, "Voltage": 1}]
    reading = battery.parse(registry(data))
    assert reading["available"] and reading["is_charging"] is False
    assert reading["voltage_mv"] == 12339 and reading["remaining_mah"] == 4781


def test_session_and_measurement_reads_always_call_the_shared_parser(fake_runner, monkeypatch):
    fake_runner.responses[tuple(battery.COMMAND)] = CmdResult(FIXTURE.read_text(), "", 0)
    original, parsed = battery.parse, []

    def parse(text, **kwargs):
        parsed.append((text, kwargs["endpoint"]))
        return original(text, **kwargs)

    monkeypatch.setattr(battery, "parse", parse)
    assert not sessions.observe()["errors"]
    result = measurement.measure(measurement.Settings(seconds=10), clock=FakeClock())
    assert result["reason"] == "insufficient_samples"
    assert [endpoint for _, endpoint in parsed] == [True, False, False]
    assert len(parsed) == fake_runner.calls.count(tuple(battery.COMMAND))
    assert all(text == FIXTURE.read_text() for text, _ in parsed)


def test_only_battery_adapter_owns_the_apple_smart_battery_command():
    import ast

    root = Path(battery.__file__).parents[1]
    owners = []
    for path in root.rglob("*.py"):
        if path.name == "demo.py":  # A fixture backend matches commands; it never reads the host.
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, (ast.List, ast.Tuple))
                and [value.value for value in node.elts if isinstance(value, ast.Constant)] == battery.COMMAND
            ):
                owners.append(str(path.relative_to(root)))
    assert owners == ["system/battery.py"]


@pytest.mark.parametrize("value", ["<plist><array>", "<plist><dict>", "<plist><data>!", "not a plist"])
def test_malformed_nested_values_fail_explicitly(value):
    with pytest.raises(battery.BatteryError, match="ioreg structure"):
        battery.parse(value)


@pytest.mark.parametrize("roots", [{}, [1], [{}, {}]])
def test_registry_requires_one_selected_dictionary(roots):
    import plistlib

    with pytest.raises(battery.BatteryError, match="ioreg structure"):
        battery.parse(plistlib.dumps(roots).decode())

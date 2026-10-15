"""Auxiliary measurement failures must not cancel Actions or fabricate watts."""

import json
from pathlib import Path

import pytest
from test_battery import FakeClock

from travel_mode import cli, measurement, sessions, state
from travel_mode.i18n import t
from travel_mode.system import battery
from travel_mode.system.shell import CmdResult, ReadError


def test_ac_preflight_skips_ioreg_and_window(fake_runner, monkeypatch):
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult("Now drawing from 'AC Power'", "", 0)
    monkeypatch.setattr(battery, "read", lambda: pytest.fail("AC must skip ioreg sampling"))
    messages = []
    clock = FakeClock()
    assert measurement.begin("work", None, measurement.Settings(), messages.append, clock=clock) is None
    assert clock.now == 0 and messages == [t("measurement.ac_skip")]
    assert fake_runner.calls == [("pmset", "-g", "ps")]


@pytest.mark.parametrize("phase", ["before", "after", "source"])
@pytest.mark.parametrize("error_type", [ReadError, battery.BatteryError])
def test_measurement_failure_continues_cli_and_is_retained_in_session_report(
    phase, error_type, tmp_paths, darwin_platform, fake_runner, monkeypatch, capsys
):
    (tmp_paths / "config.toml").write_text("[measurement]\nseconds=2\ninterval=2\nsettle_seconds=0\n")
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    reading = battery.parse((Path(__file__).parents[1] / "fixtures/battery/discharging.txt").read_text())

    def read():
        changed = any(c[:3] == ("sudo", "pmset", "-b") for c in fake_runner.calls)
        if (phase == "before" and not changed) or (phase == "after" and changed):
            raise error_type("fixture ioreg timeout")
        return reading

    monkeypatch.setattr(battery, "read", read)
    if phase == "source":
        original = measurement._read

        def source(command, **kwargs):
            if command == ["pmset", "-g", "ps"]:
                raise error_type("fixture source timeout")
            return original(command, **kwargs)

        monkeypatch.setattr(measurement, "_read", source)
    monkeypatch.setattr(measurement, "Clock", FakeClock)
    assert cli.main(["--scenario", "work", "--yes"]) == 0
    assert any(c[:3] == ("sudo", "pmset", "-b") for c in fake_runner.calls)
    saved = state.load_state()
    result = saved["measurement"]
    failed = result["before"] if phase in {"before", "source"} else result["after"]
    assert failed["reason"] == "failed" and "timeout" in failed["error"]
    assert result["saved_watts"] is result["extra_hours"] is None
    if phase in {"before", "source"}:
        assert result["after"] is None
    message = t("measurement.failed", reason=failed["error"])
    assert message in capsys.readouterr().out
    report = sessions.build_report(saved, 1, 0)
    sessions.validate_report(report)
    assert report["measurement"] == result and message in "\n".join(sessions.describe(report))
    assert "NaN" not in json.dumps(report, allow_nan=False)


def test_unexpected_measurement_exception_still_propagates(monkeypatch):
    def fail():
        raise RuntimeError("programming error")

    monkeypatch.setattr(battery, "read", fail)
    with pytest.raises(RuntimeError, match="programming error"):
        measurement.begin("work", True, measurement.Settings(), lambda text: None)


def test_after_window_skips_settling_and_ioreg_if_ac_is_connected(fake_runner):
    from test_battery import FIXTURES

    before = measurement.measure(
        measurement.Settings(seconds=2),
        clock=FakeClock(),
        first=battery.parse((FIXTURES / "discharging.txt").read_text()),
    )
    fake_runner.calls.clear()
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult("AC Power", "", 0)
    clock = FakeClock()
    result = measurement.finish(
        before, measurement.Settings(), {}, lambda st: None, lambda text: None, clock=clock
    )
    assert clock.now == 0 and result["after"]["reason"] == "external_power"
    assert tuple(battery.COMMAND) not in fake_runner.calls


def test_failure_shape_rejects_fabricated_after_window():
    failure = measurement.failed(ReadError("fixture timeout"))
    value = dict(before=failure, after=failure, saved_watts=None, extra_hours=None)
    with pytest.raises(ValueError, match="after"):
        measurement.validate_comparison(value)

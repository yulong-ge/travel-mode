"""The maintainer experiment is exercised with a strict fake backend and clock only."""

import json
from pathlib import Path

import measure_lowpower as experiment
import pytest

from battery_support import FakeClock
from conftest import FakeRunner
from travel_mode.i18n import set_language
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult

CUSTOM = (
    "Battery Power:\n Sleep On Power Button 1\n lowpowermode         {value}\n"
    " sleep                1\n lessbright           1\n"
    "AC Power:\n lowpowermode         0\n sleep                0\n"
)


class Machine(FakeRunner):
    def __init__(self, original=0):
        super().__init__(
            {
                ("pmset", "-g", "ps"): CmdResult("Battery Power", "", 0),
                ("pmset", "-g", "batt"): CmdResult("Battery Power; 80%; 3:00 remaining", "", 0),
                tuple(experiment.CPU_COMMAND): CmdResult("910001 2.0 /Applications/Demo.app/Demo", "", 0),
                tuple(experiment.native_command("brightness")): CmdResult("0.5", "", 0),
                tuple(experiment.probe_command(0)): CmdResult("/usr/bin/pmset -b lowpowermode 0", "", 0),
                tuple(experiment.probe_command(1)): CmdResult("/usr/bin/pmset -b lowpowermode 1", "", 0),
            }
        )
        self.value = original
        self.writes = []
        self.generation = 0
        self.total = 1_000_000
        self.restore_mismatch = False
        self.restore_failure = False
        self.cached = False
        self.telemetry = True

    def __call__(self, cmd, timeout=None, interactive=False):
        assert not interactive and timeout is not None
        if cmd in [experiment.write_command(0), experiment.write_command(1)]:
            self.calls.append(tuple(cmd))
            self.writes.append(int(cmd[-1]))
            if self.restore_failure and len(self.writes) > 1:
                return CmdResult("", "rollback denied", 1)
            self.value = int(cmd[-1])
            return CmdResult("", "", 0)
        if cmd == ["pmset", "-g", "custom"]:
            self.calls.append(tuple(cmd))
            raw = CUSTOM.format(value=self.value)
            if self.restore_mismatch and self.writes:
                raw = raw.replace(" sleep                1", " sleep                2")
            return CmdResult(raw, "", 0)
        if cmd == ["ioreg", "-rn", "AppleSmartBattery"]:
            self.calls.append(tuple(cmd))
            self.generation += not self.cached
            self.total += (10 if self.value else 20) * 20_000 * (not self.cached)
            raw = (Path(__file__).parents[1] / "fixtures/battery/discharging.txt").read_text()
            raw = raw.replace('"UpdateTime" = 1000', f'"UpdateTime" = {1000 + self.generation * 20}')
            if self.telemetry:
                raw = raw.rsplit("}", 1)[0] + (
                    '"PowerTelemetryData" = {'
                    f'"AccumulatedSystemLoad"={self.total},'
                    f'"SystemLoadAccumulatorCount"={100 + self.generation * 20}' + "}\n}\n"
                )
            return CmdResult(raw, "", 0)
        return super().__call__(cmd, timeout, interactive)


def run(tmp_path, machine, **kwargs):
    return experiment.run_experiment(
        tmp_path / "evidence",
        runner=machine,
        clock=FakeClock(),
        state_path=tmp_path / "absent.json",
        **kwargs,
    )


@pytest.mark.parametrize("original", [0, 1])
def test_three_stage_accumulator_sequence_fake_clock_and_exact_rollback(tmp_path, original):
    machine, clock = Machine(original), FakeClock()
    previous = shell.get_runner()
    report = experiment.run_experiment(
        tmp_path / "evidence", runner=machine, clock=clock, state_path=tmp_path / "absent.json"
    )
    assert machine.writes == [0, 1, 0, original]
    assert machine.value == original and report["rollback"] == {"verified": True}
    assert report["completed"] and shell.get_runner() is previous
    assert report["original_profiles"] == report["restored_profiles"] == CUSTOM.format(value=original)
    assert clock.sleeps.count(30) == 3 and clock.now == 135
    assert report["settings"] == dict(seconds=300, interval=5, settle_seconds=30, updates=3)
    assert [s["measurement"]["average_watts"] for s in report["stages"]] == [20, 10, 20]
    for stage in report["stages"]:
        result = stage["measurement"]
        assert result["samples"] == 3 and result["interval_count"] == 2
        assert result["capacity_source"] == "AppleRaw"
        assert stage["before"]["brightness"] == dict(available=True, value=0.5)
        assert stage["after"]["cpu"]["percent_sum"] == 2
    assert report["comparison"] == dict(
        off_mean_watts=20, on_mean_watts=10, reduction_watts=10, off_drift_watts=0
    )
    assert json.loads((tmp_path / "evidence/report.json").read_text()) == report
    probes = [tuple(experiment.probe_command(n)) for n in (0, 1)]
    first_write = machine.calls.index(tuple(experiment.write_command(0)))
    assert all(machine.calls.index(cmd) < first_write for cmd in probes)


@pytest.mark.parametrize(
    "cmd",
    [
        ["sudo", "pmset", "-b", "lowpowermode", "1"],
        ["sudo", "-n", "/usr/bin/pmset", "-b", "lowpowermode", "1"],
        ["sudo", "-n", "-k", "/usr/bin/pmset", "-a", "lowpowermode", "1"],
        ["sudo", "-n", "-k", "/usr/bin/pmset", "-c", "lowpowermode", "1"],
        ["sudo", "-n", "-k", "/usr/bin/pmset", "-b", "sleep", "1"],
        ["sudo", "-n", "-k", "/usr/bin/pmset", "-b", "lowpowermode", "2"],
        ["sudo", "-v"],
        ["sudo", "-k"],
        ["sudo", "-n", "-l"],
        ["kill", "-STOP", "910001"],
        ["osascript", "-e", "display notification"],
        ["brightness", "0.5"],
        ["pmset", "-b", "lowpowermode", "1"],
    ],
)
def test_guard_rejects_every_other_mutation_or_authentication(cmd):
    backend = FakeRunner()
    guard = experiment.LowPowerGuard(backend)
    with pytest.raises(experiment.GuardViolation):
        guard(cmd, timeout=10)
    assert backend.calls == []


def test_guard_refuses_interactive_and_unbounded_calls():
    guard = experiment.LowPowerGuard(FakeRunner())
    for kwargs in ({}, {"timeout": 31}, {"timeout": 10, "interactive": True}):
        with pytest.raises(experiment.GuardViolation):
            guard(experiment.write_command(0), **kwargs)


def test_sudo_ignore_cache_still_pins_both_executables(monkeypatch):
    monkeypatch.setattr(shell, "_is_executable", lambda _: True)
    for cmd in (experiment.write_command(0), experiment.probe_command(1)):
        resolved = shell.resolve_command(cmd)
        assert resolved[0] == "/usr/bin/sudo" and "/usr/bin/pmset" in resolved


def test_external_power_refuses_before_any_write(tmp_path):
    machine = Machine()
    machine.responses[("pmset", "-g", "ps")] = CmdResult("AC Power", "", 0)
    with pytest.raises(experiment.ExperimentError, match=r"电池|Battery"):
        run(tmp_path, machine)
    assert not machine.writes


def test_active_snapshot_refuses_before_system_commands(tmp_path):
    target = tmp_path / "active.json"
    target.write_text('{"version": 1}')
    machine = Machine()
    with pytest.raises(experiment.ExperimentError, match=r"恢复记录|recovery record"):
        experiment.run_experiment(tmp_path / "evidence", runner=machine, state_path=target)
    assert machine.calls == []


def test_passwordless_probe_failure_refuses_before_write(tmp_path):
    machine = Machine()
    machine.responses[tuple(experiment.probe_command(1))] = CmdResult("", "password required", 1)
    with pytest.raises(experiment.ExperimentError, match="password required"):
        run(tmp_path, machine)
    assert not machine.writes


@pytest.mark.parametrize("error", [shell.ReadError("ioreg timeout"), KeyboardInterrupt(), ValueError("bug")])
def test_exception_or_ctrl_c_rolls_back_and_records_failure(tmp_path, monkeypatch, error):
    machine = Machine(1)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(experiment.measurement, "measure", fail)
    with pytest.raises(type(error)):
        run(tmp_path, machine)
    assert machine.writes == [0, 1]
    report = json.loads((tmp_path / "evidence/report.json").read_text())
    assert not report["completed"] and report["rollback"]["verified"]
    assert type(error).__name__ in report["error"]


@pytest.mark.parametrize("failure", ["restore_mismatch", "restore_failure"])
def test_rollback_failures_raise_with_cause_and_evidence(tmp_path, monkeypatch, failure):
    machine = Machine(1)
    setattr(machine, failure, True)
    monkeypatch.setattr(
        experiment.measurement,
        "measure",
        lambda *a, **k: (_ for _ in ()).throw(shell.ReadError("sampling failed")),
    )
    set_language("en")
    with pytest.raises(experiment.ExperimentError, match="ROLLBACK FAILED") as error:
        run(tmp_path, machine)
    assert error.value.__cause__ is not None
    assert error.value.__cause__.__context__.args == ("sampling failed",)
    report = json.loads((tmp_path / "evidence/report.json").read_text())
    assert not report["rollback"]["verified"] and not report["completed"]


@pytest.mark.parametrize("mode", ["cached", "telemetry_missing"])
def test_inadequate_telemetry_invalidates_experiment_and_restores(tmp_path, mode):
    machine = Machine(1)
    machine.cached = mode == "cached"
    machine.telemetry = mode != "telemetry_missing"
    with pytest.raises(experiment.ExperimentError, match="SystemLoad"):
        run(tmp_path, machine)
    assert machine.writes == [0, 1] and machine.value == 1


def test_ac_during_sampling_invalidates_run_but_still_restores(tmp_path, monkeypatch):
    machine = Machine(1)
    original_measure = experiment.measurement.measure

    def attach(*args, **kwargs):
        result = original_measure(*args, **kwargs)
        machine.responses[("pmset", "-g", "ps")] = CmdResult("AC Power", "", 0)
        return result

    monkeypatch.setattr(experiment.measurement, "measure", attach)
    with pytest.raises(experiment.ExperimentError, match=r"电池|Battery"):
        run(tmp_path, machine)
    assert machine.writes == [0, 1]


def test_unavailable_native_brightness_is_explicit_and_does_not_change_brightness(tmp_path):
    machine = Machine()
    machine.responses[tuple(experiment.native_command("brightness"))] = CmdResult("", "unsupported", 1)
    report = run(tmp_path, machine)
    assert report["stages"][0]["before"]["brightness"] == dict(available=False, error="unsupported", rc=1)


def test_main_rollback_failure_is_loud_and_nonzero(tmp_path, monkeypatch, capsys):
    machine = Machine(1)
    machine.restore_mismatch = True
    shell.set_runner(machine)
    monkeypatch.setattr(experiment.sys, "platform", "darwin")
    monkeypatch.setattr(experiment.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path / "evidence"))
    monkeypatch.setattr(experiment.state, "load_state", lambda path=None: None)
    monkeypatch.setattr(experiment.measurement, "Clock", FakeClock)
    assert experiment.main([]) == 1
    assert "ROLLBACK FAILED" in capsys.readouterr().err


def test_successful_setter_with_unchanged_value_is_not_measured(tmp_path, monkeypatch):
    machine = Machine(1)
    base = machine.__call__

    def no_change(cmd, **kwargs):
        result = base(cmd, **kwargs)
        machine.value = 1
        return result

    with pytest.raises(experiment.ExperimentError, match=r"未变为|did not become"):
        run(tmp_path, no_change)
    assert machine.writes == [0, 1]


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_plan_executes_no_system_commands_or_state_reads(monkeypatch, capsys, lang):
    def forbidden(*args, **kwargs):
        pytest.fail("--plan must not read state or execute commands")

    monkeypatch.setattr(experiment.state, "load_state", forbidden)
    monkeypatch.setattr(shell, "_runner", forbidden)
    assert experiment.main(["--plan", "--lang", lang]) == 0
    output = capsys.readouterr().out
    assert output.count("sudo -n -k /usr/bin/pmset -b lowpowermode") == 4
    assert "300" in output and "30" in output and "0→1→0" in output
    assert "-a " not in output and "-c " not in output

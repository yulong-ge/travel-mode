#!/usr/bin/env python
"""Maintainer-only, guarded 0→1→0 battery Low Power Mode experiment. --plan is inert."""

from __future__ import annotations

import argparse
import json
import math
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness_evidence import write_json

from travel_mode import measurement, state
from travel_mode.i18n import set_language, t
from travel_mode.system import battery, shell
from travel_mode.system.health import native_command

SEQUENCE = (0, 1, 0)
SETTINGS = measurement.Settings(seconds=300, interval=5, settle_seconds=30, updates=3)
CPU_COMMAND = ["ps", "-axo", "pid=,%cpu=,comm="]


class ExperimentError(RuntimeError):
    """The experiment cannot establish a valid result."""


class GuardViolation(PermissionError):
    """An operation lies outside the experiment's exact command allowlist."""


def write_command(value):
    # With a command, -k ignores cached credentials without clearing/updating them.
    # Together with -n, only passwordless authorization can execute the write.
    return ["sudo", "-n", "-k", "/usr/bin/pmset", "-b", "lowpowermode", str(value)]


def probe_command(value):
    return ["sudo", "-n", "-k", "-l", *write_command(value)[3:]]


class LowPowerGuard:
    """Only this battery key may change; sudo can never prompt or refresh credentials."""

    def __init__(self, backend):
        self.backend = backend
        self.calls = []

    def __call__(self, cmd, timeout=None, interactive=False):
        allowed = [
            ["pmset", "-g", "custom"],
            ["pmset", "-g", "ps"],
            ["pmset", "-g", "batt"],
            ["ioreg", "-rn", "AppleSmartBattery"],
            CPU_COMMAND,
            native_command("brightness"),
            *(write_command(value) for value in (0, 1)),
            *(probe_command(value) for value in (0, 1)),
        ]
        if interactive or cmd not in allowed or timeout is None or not 0 < timeout <= 30:
            raise GuardViolation(t("lowpower.guard", command=shlex.join(cmd)))
        self.calls.append(list(cmd))
        return self.backend(cmd, timeout=timeout, interactive=False)


def profile_section(raw, heading):
    """Preserve every byte of the selected section, including spacing/newlines."""
    sections = {}
    current = None
    for line in raw.splitlines(keepends=True):
        if line and not line[0].isspace() and line.rstrip().endswith("Power:"):
            current = line.strip()
            if current in sections:
                raise ExperimentError(t("lowpower.profile_invalid", detail=current))
            sections[current] = []
        if current is not None:
            sections[current].append(line)
    if heading not in sections:
        raise ExperimentError(t("lowpower.profile_invalid", detail=heading))
    return "".join(sections[heading])


def original_value(section):
    rows = [line.split() for line in section.splitlines() if line.split()[:1] == ["lowpowermode"]]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] not in {"0", "1"}:
        raise ExperimentError(t("lowpower.profile_invalid", detail="Battery Power.lowpowermode"))
    return int(rows[0][1])


def checked(guard, cmd):
    result = guard(cmd, timeout=10)
    if result.rc:
        raise ExperimentError(
            t(
                "lowpower.command_failed",
                command=shlex.join(cmd),
                rc=result.rc,
                detail=result.stderr or result.stdout,
            )
        )
    return result.stdout


def require_battery():
    if measurement.on_external_power():
        raise ExperimentError(t("lowpower.ac_refused"))


def context(guard):
    result = guard(native_command("brightness"), timeout=5)
    if result.rc:
        brightness = dict(available=False, error=result.stderr or result.stdout, rc=result.rc)
    else:
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ExperimentError(t("lowpower.brightness_invalid", detail=result.stdout)) from exc
        if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ExperimentError(t("lowpower.brightness_invalid", detail=repr(value)))
        brightness = dict(available=True, value=value)
    raw = checked(guard, CPU_COMMAND)
    rows = []
    for line in raw.splitlines():
        try:
            pid, cpu, command = line.split(None, 2)
            row = dict(pid=int(pid), cpu_percent=float(cpu), command=command)
        except ValueError as exc:
            raise ExperimentError(t("lowpower.cpu_invalid", detail=line)) from exc
        if row["pid"] <= 0 or not math.isfinite(row["cpu_percent"]) or row["cpu_percent"] < 0:
            raise ExperimentError(t("lowpower.cpu_invalid", detail=line))
        rows.append(row)
    if not rows:
        raise ExperimentError(t("lowpower.cpu_invalid", detail="empty ps snapshot"))
    return dict(
        brightness=brightness,
        cpu=dict(
            percent_sum=sum(row["cpu_percent"] for row in rows),
            top=sorted(rows, key=lambda row: row["cpu_percent"], reverse=True)[:5],
        ),
    )


def run_experiment(output, *, runner=None, clock=None, state_path=None):
    """Injectable backend/clock; all exit paths after a write attempt verify rollback."""
    if state.load_state(state_path) is not None:
        raise ExperimentError(t("lowpower.active_state"))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    previous = shell.get_runner()
    guard = LowPowerGuard(runner if runner is not None else previous)
    clock = clock or measurement.Clock()
    report = dict(
        started_at=datetime.now(timezone.utc).isoformat(),
        sequence=list(SEQUENCE),
        settings=vars(SETTINGS),
        stages=[],
        completed=False,
        rollback=None,
    )
    shell.set_runner(guard)
    try:
        require_battery()
        raw = checked(guard, ["pmset", "-g", "custom"])
        saved_battery = profile_section(raw, "Battery Power:")
        saved_ac = profile_section(raw, "AC Power:")
        original = original_value(saved_battery)
        report.update(original_lowpowermode=original, original_profiles=raw)
        # Probe BOTH settings before attempting any write, including the rollback target.
        for value in (0, 1):
            checked(guard, probe_command(value))
        print(t("lowpower.stable_load"), flush=True)
        try:
            for index, value in enumerate(SEQUENCE, 1):
                require_battery()
                stage = dict(stage=index, lowpowermode=value)
                report["stages"].append(stage)
                print(t("lowpower.stage", stage=index, value=value), flush=True)
                checked(guard, write_command(value))
                applied = checked(guard, ["pmset", "-g", "custom"])
                if original_value(profile_section(applied, "Battery Power:")) != value:
                    raise ExperimentError(t("lowpower.switch_mismatch", value=value))
                clock.sleep(SETTINGS.settle_seconds)
                require_battery()
                stage["before"] = context(guard)
                result = measurement.measure(SETTINGS, clock=clock)
                stage["measurement"] = result
                stage["after"] = context(guard)
                require_battery()
                if not result["available"] or result.get("power_source") != "PowerTelemetryData.SystemLoad":
                    raise ExperimentError(t("lowpower.measure_invalid", detail=json.dumps(result)))
                print(measurement.describe(result), flush=True)
                print(t("lowpower.samples", samples=result["samples"], polls=result["polls"]), flush=True)
                for interval in result["intervals"]:
                    print(
                        t("lowpower.interval", watts=interval["average_watts"], seconds=interval["seconds"]),
                        flush=True,
                    )
            means = [stage["measurement"]["average_watts"] for stage in report["stages"]]
            report["comparison"] = dict(
                off_mean_watts=(means[0] + means[2]) / 2,
                on_mean_watts=means[1],
                reduction_watts=(means[0] + means[2]) / 2 - means[1],
                off_drift_watts=means[2] - means[0],
            )
            report["completed"] = True
        finally:
            # Also runs if the first write fails, a read fails, or Ctrl-C interrupts sampling.
            report["rollback"] = dict(verified=False)
            try:
                checked(guard, write_command(original))
                restored = checked(guard, ["pmset", "-g", "custom"])
                report["restored_profiles"] = restored
                if profile_section(restored, "Battery Power:") != saved_battery:
                    raise ExperimentError(t("lowpower.rollback_mismatch", profile="Battery Power"))
                if profile_section(restored, "AC Power:") != saved_ac:
                    raise ExperimentError(t("lowpower.rollback_mismatch", profile="AC Power"))
                report["rollback"]["verified"] = True
            except (ExperimentError, GuardViolation, OSError) as exc:
                report["rollback"]["error"] = str(exc)
                raise ExperimentError(t("lowpower.rollback_failed", detail=str(exc))) from exc
    finally:
        error = sys.exc_info()[1]
        if error is not None:
            report["error"] = f"{type(error).__name__}: {error}"
            report["completed"] = False
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["commands"] = guard.calls
        shell.set_runner(previous)
        write_json(output / "report.json", report)
        print(t("lowpower.evidence", path=output / "report.json"), flush=True)
    return report


def print_plan():
    print(t("lowpower.plan"))
    print(t("lowpower.stable_load"))
    for cmd in (["pmset", "-g", "ps"], ["pmset", "-g", "custom"], probe_command(0), probe_command(1)):
        print(shlex.join(cmd))
    for value in SEQUENCE:
        print(shlex.join(write_command(value)))
        print(t("lowpower.plan_stage"))
        print(shlex.join(native_command("brightness")))
        print(shlex.join(CPU_COMMAND))
        print("ioreg -rn AppleSmartBattery")
        print("pmset -g batt")
    print(t("lowpower.plan_restore"))
    print("sudo -n -k /usr/bin/pmset -b lowpowermode <original 0|1>")
    print("pmset -g custom")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--lang", choices=("en", "zh"), default="en")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    set_language(args.lang)
    if args.plan:
        print_plan()
        return 0
    if sys.platform != "darwin":
        raise ExperimentError(t("lowpower.macos_only"))
    output = args.output or Path("/tmp/lowpower-measurements") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    try:
        report = run_experiment(output)
    except (
        ExperimentError,
        GuardViolation,
        shell.ReadError,
        battery.BatteryError,
        OSError,
        state.StateError,
        KeyboardInterrupt,
    ) as exc:
        print(t("lowpower.failed", detail=str(exc) or type(exc).__name__), file=sys.stderr)
        return 1
    print(t("lowpower.summary", **report["comparison"]))
    print(t("lowpower.restored"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Battery-only harness policy and diagnostics; never a system mutation path."""

from __future__ import annotations

import json
import math
from pathlib import Path

from e2e_guard import LSREGISTER, PROCESS_QUERY, QUIT_EVENT, GuardRunner

from travel_mode.measurement import on_external_power
from travel_mode.system import battery
from travel_mode.system.shell import _read

MIN_SAVING_WATTS = 1.0
WARMUP_SECONDS = 5
QUICK_SELECTION = {
    "q-measure": True,
    "q-power": False,
    "q-apps": True,
    "q-svc": True,
    "q-sync": False,
    "q-updater": False,
    "q-spot": False,
    "q-net": False,
}


def require_battery():
    if on_external_power():
        raise RuntimeError("Battery measurement requires battery power; disconnect AC before running.")
    reading = battery.read()
    if not reading["available"]:
        raise RuntimeError("Battery measurement unavailable: " + battery.describe(reading))
    return reading


def evidence(runner, comparison):
    """Retain numerical readings only, never raw ioreg device identifiers."""
    samples, phase = [], "before_freeze"
    for command, result in runner.results:
        if command[:2] in (("kill", "-STOP"), ("kill", "-CONT")):
            identity = runner.owned.get(int(command[2]))
            if identity and "/TravelModeE2E-Burn.app/" in identity[1]:
                phase = "after_freeze" if command[1] == "-STOP" else "after_restore"
        if command == tuple(battery.COMMAND):
            try:
                reading = battery.parse(result.stdout) if result.rc == 0 else {"error": result.stderr}
            except battery.BatteryError as error:
                reading = {"error": str(error)}
            samples.append({"phase": phase, **reading})
    return {"minimum_saving_watts": MIN_SAVING_WATTS, "comparison": comparison, "samples": samples}


def assert_comparison(runner, comparison):
    data = evidence(runner, comparison)
    print("BATTERY MEASUREMENT: " + json.dumps(data, ensure_ascii=False), flush=True)
    if any(row.get("reason") in {"external_power", "charging"} for row in data["samples"]):
        raise AssertionError("Invalid battery run: external power was observed during the run.")
    for command, result in runner.results:
        if (
            command in (("/usr/bin/pmset", "-g", "ps"), ("/usr/bin/pmset", "-g", "batt"))
            and "AC Power" in result.stdout
        ):
            raise AssertionError("Invalid battery run: external power was observed during the run.")
    if not comparison:
        raise AssertionError(
            "Before/after battery measurements are missing; see BATTERY MEASUREMENT samples."
        )
    for phase in ("before", "after"):
        row = comparison.get(phase)
        if not row or row.get("available") is not True:
            raise AssertionError(f"{phase} measurement failed; see BATTERY MEASUREMENT samples.")
        if row.get("capacity_source") not in ("AppleRaw", "BatteryData"):
            raise AssertionError(f"{phase} measurement lacks a capacity source.")
        if row.get("power_source") not in ("PowerTelemetryData.SystemLoad", "voltage_current"):
            raise AssertionError(f"{phase} measurement lacks a power source.")
        if row.get("interval_count", 0) < 2 or row.get("samples", 0) < 3:
            raise AssertionError(f"{phase} measurement lacks three fresh updates / two intervals.")
        print(f"{phase} intervals: " + json.dumps(row["intervals"]), flush=True)
    saving = comparison.get("saved_watts")
    if not isinstance(saving, (int, float)) or not math.isfinite(saving) or saving < MIN_SAVING_WATTS:
        raise AssertionError(
            f"Measured saving must be >= {MIN_SAVING_WATTS:.1f} W after freezing two busy sh processes; "
            "this is a conservative harness threshold, not a hardware guarantee. "
            "Inspect the before/after samples above for workload drift or measurement failure."
        )
    return data


def assert_report(report):
    if report["power_status"] != "battery_only":
        raise AssertionError(
            f"Invalid battery run: session power_status={report['power_status']}; "
            "AC exposure or incomplete battery-source evidence invalidates discharge statistics."
        )
    for phase in ("start", "end"):
        row = report[phase]
        if row is None or type(row["remaining_mah"]) is not int or row["external"] or row["charging"]:
            raise AssertionError(f"Session {phase} lacks a valid battery-only mAh endpoint.")
        if row["errors"]:
            raise AssertionError(f"Session {phase} read failed: {row['errors']}")
    if report["average_watts"] is None or report["restore"]["failed"]:
        raise AssertionError("Session report must contain average power and a successful restore.")
    if report["measurement"]["after"].get("power_source") == "PowerTelemetryData.SystemLoad" and (
        report["average_scope"] != "session"
        or report.get("average_source") != "PowerTelemetryData.SystemLoad"
    ):
        raise AssertionError("Telemetry hardware must provide a whole-session accumulator mean.")


def assert_battery_interval(started, ended):
    from travel_mode.sessions import log_window

    # A session starts at its first Action, after the first measurement. Check
    # setup and that window too, including AC intervals between samples.
    _, coverage = log_window(_read(["pmset", "-g", "log"], timeout=5), started, ended)
    if coverage != "battery_only":
        raise AssertionError(f"Invalid battery run: full-run power coverage={coverage}.")
    return coverage


def print_plan():
    from e2e_local import fixture_paths

    print("Battery-only work mode: CLI and TUI; AC is refused before fixture startup.")
    print("Maintainer entry: uv run python scripts/e2e_ui.py --battery-measure")
    print("No pmset writes, sudo (including probes), power/brightness round trips, or display-off.")
    print("CLI: answer no to every power/AC/disablesleep/hibernatemode/brightness/display-off prompt.")
    print("TUI quick selection: " + json.dumps(QUICK_SELECTION))
    print("Create private sh copies inside Burn.app: main starts helper; both run 'while :; do :; done'.")
    print(
        f"Warm up for {WARMUP_SECONDS} s; require 3 fresh updates / 2 intervals (240 s timeout, 5 s polls); "
        "freeze fixture apps; settle 10 s; repeat the same measurement."
    )
    print(
        f"Require available before/after windows with capacity_source and saving >= {MIN_SAVING_WATTS:.1f} W."
    )
    print(
        "Two busy interpreters provide a controlled load; "
        "1 W is a conservative acceptance threshold, not a guarantee."
    )
    print(
        "A failed threshold prints both window averages and every numerical ioreg sample; "
        "no synthetic values."
    )
    root = Path("/tmp/travel-mode-e2e-PLAN")
    guard = GuardRunner(
        None,
        root,
        fixture_paths(root, battery_measure=True),
        root / "agent.plist",
        plan=True,
        battery_measure=True,
    )
    for command in [
        ["pmset", "-g", "ps"],
        battery.COMMAND,
        ["pmset", "-g", "custom"],
        ["pmset", "-g"],
        PROCESS_QUERY,
        ["launchctl", "list"],
        ["osacompile", "-s", "-o", str(guard.graceful), "-e", "on idle", "-e", "return 1", "-e", "end idle"],
        ["codesign", "--force", "--sign", "-", str(guard.graceful)],
        ["open", "-g", "-j", str(guard.graceful)],
        QUIT_EVENT,
        *[[exe] for exe in guard.executables],
        ["launchctl", "load", str(guard.plist)],
    ]:
        guard(command)
    print("CLI apply: uv run travel-mode --no-tui --lang en (scenario question: work)")
    print("TUI apply: work picker -> selected fixture-only quick action; no power page action.")
    for index, executable in enumerate(guard.shells, 900001):
        guard.owned[index] = ("<birth>", executable)
    for phase in ("before", "after"):
        print(
            f"{phase} window: up to 48 polls (plus the open TUI's existing 30 s reads), "
            "stop at 3 fresh updates:"
        )
        for _ in range(48):
            guard(battery.COMMAND)
        guard(["pmset", "-g", "batt"])
        if phase == "before":
            guard(QUIT_EVENT)
            for pid, (_, executable) in guard.owned.items():
                if any(f"/TravelModeE2E-{name}.app/" in executable for name in ("Freeze", "Burn")):
                    guard(["kill", "-STOP", str(pid)])
            guard(["launchctl", "unload", str(guard.plist)])
    print("Off: uv run travel-mode off --lang en; TUI: Network/Restore -> off.")
    for pid, (_, executable) in guard.owned.items():
        if any(f"/TravelModeE2E-{name}.app/" in executable for name in ("Freeze", "Burn")):
            guard(["kill", "-CONT", str(pid)])
    guard(["launchctl", "load", str(guard.plist)])
    guard(["open", str(guard.graceful)])
    guard(["pmset", "-g", "log"])
    print(
        "Require battery_only report, real mAh endpoints, average watts, restored Burn PID/birth identities."
    )
    print(
        "Check log coverage from preflight through off, including warmup/first window; "
        "AC/unknown invalidates run."
    )
    print(
        "Cleanup uses existing identity-checked CONT/TERM/KILL, applet unregister and agent unload; "
        "no power rollback."
    )
    for pid in guard.owned:
        for signal in ("-CONT", "-TERM", "-KILL"):
            guard(["kill", signal, str(pid)])
    guard(["launchctl", "unload", str(guard.plist)])
    guard(QUIT_EVENT)
    guard([LSREGISTER, "-u", str(guard.graceful)])

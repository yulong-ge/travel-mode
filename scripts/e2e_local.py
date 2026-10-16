#!/usr/bin/env python
"""Opt-in guarded fixture/power round trips; --plan executes no system commands.

Only a maintainer may run the real mode after reviewing this file and e2e_guard.
Default tests inject the stateful fake backend and isolated paths.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import pwd
import shlex
import shutil
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime
from difflib import unified_diff
from pathlib import Path
from unittest.mock import patch

# Support both `uv run python scripts/e2e_local.py` and imports in default tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_guard import (
    GRACEFUL_ID,
    LABEL,
    LSREGISTER,
    PROCESS_QUERY,
    QUIT_EVENT,
    STATUS_QUERY,
    GuardRunner,
    LocalBackend,
)
from e2e_isolation import discovery_scope, write_fixture_config

from travel_mode import state
from travel_mode.actions.apps import ForceQuitApp, FreezeApp, QuitApp, UnloadAgent
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.actions.power import EnableSleep, SetBrightness, SetPowerProfile
from travel_mode.flows.restore import run_restore
from travel_mode.system import apps, brightness, pmset, shell


class RestrictedBrightness:
    """Explicit native boundary: only the test target and captured original."""

    def __init__(self, native, original):
        self.native, self.original = native, original
        self.calls = []

    def get(self):
        return self.native.get()

    def set(self, value):
        if value not in (0.15, self.original):
            raise PermissionError(f"Native brightness refused unexpected value: {value}")
        self.calls.append(value)
        return self.native.set(value)


class ReadOnlyBrightness:
    """Native capability reads during ordinary UI/app/power phases; setters fail."""

    def __init__(self, native):
        self.native = native

    def get(self):
        return self.native.get()

    def set(self, value):
        raise PermissionError("Native brightness writes require the dedicated restricted round trip")


class CleanupError(RuntimeError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("CLEANUP FAILED: " + "; ".join(str(error) for error in errors))


def preflight(root, state_file, real_state):
    if real_state.exists():
        raise RuntimeError(f"Active user state exists; refusing e2e: {real_state}")
    root = root.resolve()
    if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()) or not root.name.startswith(
        "travel-mode-e2e-"
    ):
        raise ValueError(f"Fixture root must be a dedicated temporary directory: {root}")
    if not state_file.resolve().is_relative_to(root):
        raise ValueError(f"Isolated state escapes the temporary root: {state_file}")


@contextmanager
def product_environment(root, runner):
    (root / "home/Library/LaunchAgents").mkdir(parents=True, exist_ok=True)
    values = {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
        "TRAVEL_MODE_CONFIG": str(root / "config.toml"),
        "TRAVEL_MODE_STATE": str(root / "state.json"),
        "TRAVEL_MODE_APP_DIRS": str(root),
    }
    previous = {key: os.environ.get(key) for key in values}
    previous_runner = shell.get_runner()
    previous_override = state._STATE_PATH_OVERRIDE
    try:
        os.environ.update(values)
        state.set_state_path_override(root / "state.json")
        with discovery_scope(root, runner) as discovery:
            shell.set_runner(discovery)
            yield
    finally:
        shell.set_runner(previous_runner)
        state.set_state_path_override(previous_override)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def fixture_paths(root, *, battery_measure=False):
    names = ("Quit", "Freeze", "Burn") if battery_measure else ("Quit", "Freeze")
    return tuple(root / f"TravelModeE2E-{name}.app" for name in names)


def create_fixtures(root, *, battery_measure=False):
    for app in fixture_paths(root, battery_measure=battery_measure):
        kind = app.stem.removeprefix("TravelModeE2E-").lower()
        main = app / "Contents/MacOS"
        helper = app / "Contents/Helpers"
        main.mkdir(parents=True)
        helper.mkdir()
        # Private interpreter copies keep `ps comm` inside the bundle, which lets
        # the product verify executable identity without matching arbitrary argv.
        for directory in (main, helper):
            shutil.copyfile("/bin/sh", directory / "sh")
            (directory / "sh").chmod(0o700)
        (app / "Contents/Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleIdentifier": f"com.example.travelmode-e2e.{kind}",
                    "CFBundleExecutable": "loop",
                    "CFBundleName": app.stem,
                    "CFBundlePackageType": "APPL",
                }
            )
        )
        loop = "while :; do :; done" if kind == "burn" else "while :; do /bin/sleep 0.2; done"
        (helper / "loop").write_text(f"#!{helper}/sh\n{loop}\n")
        (helper / "loop").chmod(0o700)
        (main / "loop").write_text(f'#!{main}/sh\n"{helper}/loop" &\n{loop}\n')
        (main / "loop").chmod(0o700)
    agent = root / "agent.sh"
    agent.write_text("#!/bin/sh\nwhile :; do /bin/sleep 0.2; done\n")
    agent.chmod(0o700)
    (root / "agent.plist").write_bytes(
        plistlib.dumps({"Label": LABEL, "ProgramArguments": [str(agent)], "RunAtLoad": True})
    )
    agents = root / "home/Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{LABEL}.plist").symlink_to(root / "agent.plist")
    write_fixture_config(root, battery_measure=battery_measure)


class Harness:
    def __init__(
        self,
        root,
        runner,
        real_state,
        *,
        pause=time.sleep,
        checkpoint=lambda stage: None,
        brightness_loader=None,
    ):
        root = root.resolve()
        self.root, self.runner, self.real_state = root, runner, real_state
        self.state_file = root / "state.json"
        self.pause, self.checkpoint = pause, checkpoint
        self.plist = root / "agent.plist"
        self.apps = fixture_paths(root)
        self.original_power = None
        self.original_values = None
        self.power_dirty = False
        self.agent_attempted = False
        self.fixture_attempted = False
        self.before = None
        self.graceful_attempted = False
        self.rollback_commands = []
        self.original_sleep = None
        self.brightness_loader = brightness_loader
        self.brightness_boundary = None
        self.brightness_dirty = False
        self.brightness_report = {"status": "NOT RUN"}
        self.skips = []

    def skip(self, stage, reason):
        self.skips.append(dict(stage=stage, reason=reason))
        print(f"SKIP {stage}: {reason}", flush=True)

    def command(self, cmd):
        result = self.runner(cmd, timeout=30)
        if result.rc:
            raise RuntimeError(f"Command failed ({result.rc}): {cmd!r}: {result.stderr or result.stdout}")
        return result.stdout

    def check(self, condition, message):
        if not condition:
            raise AssertionError(message)
        print(f"PASS: {message}")

    def check_power(self, condition, message):
        if not condition:
            after = self.command(["pmset", "-g", "custom"])
            print("pmset -g custom unified diff (before/after):")
            print(
                "".join(
                    unified_diff(
                        self.original_power.splitlines(keepends=True),
                        after.splitlines(keepends=True),
                        fromfile="before/pmset-custom",
                        tofile="after/pmset-custom",
                    )
                ),
                end="\n",
            )
            print(f"SleepDisabled: before={self.original_sleep!r}, after={pmset.sleep_disabled()!r}")
        self.check(condition, message)

    def check_profile(self, before, after, targets, label):
        self.check_power(
            all(after.get(key) == value for key, value in targets.items()),
            f"{label} selected power targets reached",
        )
        allowed = set(targets)
        # macOS can derive SleepServices from powernap. This exception is local
        # to the selected profile and requires the same observed transition.
        if (
            "powernap" in targets
            and before["powernap"] != after["powernap"]
            and before.get("SleepServices") == before["powernap"]
            and after.get("SleepServices") == after["powernap"]
        ):
            allowed.add("SleepServices")
        self.check_power(
            before.keys() == after.keys()
            and all(after[key] == value for key, value in before.items() if key not in allowed),
            f"{label} unselected keys unchanged (SleepServices may follow changed powernap)",
        )

    def check_brightness(self, condition, message, *, observed=None):
        if not condition:
            if observed is None:
                observed = self.brightness_boundary.get()
            print(
                f"Brightness: original={self.brightness_boundary.original!r}, "
                f"target={self.brightness_report['target']!r}, readback={observed!r}"
            )
        self.check(condition, message)

    def wait(self, predicate, message):
        # Only process startup/termination and launchd observation are transient.
        for _ in range(40):
            if predicate():
                self.check(True, message)
                return
            self.pause(0.1)
        raise AssertionError(f"Timed out: {message}")

    def foreign_snapshot(self):
        output = self.command(STATUS_QUERY)
        rows = {}
        for line in output.splitlines():
            parts = line.split(None, 2)
            if len(parts) != 3 or not parts[0].isdigit():
                raise ValueError(f"Invalid status snapshot row: {line!r}")
            pid = int(parts[0])
            if pid not in self.runner.owned:
                rows[pid] = (parts[1].startswith("T"), parts[2])
        return rows

    def assert_foreign(self):
        after = self.foreign_snapshot()
        self.check(
            all(
                after[pid][0] == status[0]
                for pid, status in self.before.items()
                if pid in after and after[pid][1] == status[1]
            ),
            "No observed stop/resume transitions in other processes",
        )

    def stage(self, name):
        self.checkpoint(name)
        if self.runner.tripped:
            raise RuntimeError("Guard violation latched; aborting e2e")

    def emit(self, kind, message):
        print(f"{kind}: {message}")

    def off(self, snapshot):
        if any(snapshot.get(key) for key in ("batt_prof", "ac_prof", "sleepdisabled")):
            check = self.check_power
        elif snapshot.get("brightness"):
            check = self.check_brightness
        else:
            check = self.check
        _, failures = run_restore(snapshot, self.emit)
        check(failures == 0, "Product restore has no failed items")
        check(
            not any(
                snapshot.get(key)
                for key in (
                    "quit_apps",
                    "frozen_apps",
                    "frozen_pids",
                    "unloaded_svcs",
                    "batt_prof",
                    "pending_commands",
                    "ac_prof",
                    "sleepdisabled",
                    "brightness",
                )
            ),
            "All recovery receipts cleared",
        )
        self.state_file.unlink()
        check(not self.state_file.exists(), "Isolated state file removed after restore")

    def sandbox(self):
        if self.agent_loaded():
            raise RuntimeError(f"Fixture agent label already loaded; refusing to touch it: {LABEL}")
        self.fixture_attempted = True
        create_fixtures(self.root)
        self.stage("fixtures")
        for executable in self.runner.executables:
            self.command([executable])
        self.wait(
            lambda: len([row for row in self.runner.observe().values() if row[2] in self.runner.shells]) == 4,
            "Both fake apps and helpers started",
        )
        self.stage("started")
        self.agent_attempted = True
        self.command(["launchctl", "load", str(self.plist)])
        self.wait(lambda: self.agent_loaded(), "Fake agent loaded")
        self.stage("agent")
        self.before = self.foreign_snapshot()
        for expected in self.apps:
            self.check(apps.app_path(expected.stem) == str(expected), "Product resolves exact fixture app")
        st = state.new_state()
        executor = Executor(Context(st))
        self.check(executor.execute(ForceQuitApp(str(self.apps[0]))), "Product quit action succeeded")
        self.stage("quit")
        self.check(executor.execute(FreezeApp(str(self.apps[1]))), "Product freeze action succeeded")
        self.stage("freeze")
        _, rc = executor.execute(UnloadAgent(str(self.plist)))
        self.check(rc == 0, "Product unload action succeeded")
        self.stage("unload")
        rows = self.runner.observe()
        quit_shells = {str(self.apps[0] / f"Contents/{d}/sh") for d in ("MacOS", "Helpers")}
        freeze_shells = {str(self.apps[1] / f"Contents/{d}/sh") for d in ("MacOS", "Helpers")}
        self.check(not any(row[2] in quit_shells for row in rows.values()), "Quit main and helper exited")
        frozen = {pid: row for pid, row in rows.items() if row[2] in freeze_shells}
        print("Frozen process table:", frozen)
        self.check(
            len(frozen) == 2 and all(row[0].startswith("T") for row in frozen.values()),
            "Freeze main and helper are stopped",
        )
        self.check(not self.agent_loaded(), "Fake agent unloaded")
        saved = state.load_state()
        self.check(
            saved["quit_apps"] == [str(self.apps[0])]
            and saved["unloaded_svcs"] == [str(self.plist)]
            and {item["pid"] for item in saved["frozen_pids"]} == set(frozen),
            "State records exact fixture receipts",
        )
        self.assert_foreign()
        self.off(st)
        self.stage("off")
        self.wait(lambda: self.agent_loaded(), "Fake agent reloaded")
        self.wait(
            lambda: len([row for row in self.runner.observe().values() if row[2] in self.runner.shells]) == 4,
            "Quit app reopened directly",
        )
        rows = self.runner.observe()
        print("Restored process table:", {pid: rows.get(pid) for pid in frozen})
        self.check(
            all(pid in rows and not rows[pid][0].startswith("T") for pid in frozen),
            "Same freeze main and helper resumed",
        )
        self.assert_foreign()

    def agent_loaded(self):
        return any(line.split()[-1:] == [LABEL] for line in self.command(["launchctl", "list"]).splitlines())

    def graceful(self):
        self.graceful_attempted = True
        create_graceful(self.root, self.runner)
        self.command(["open", "-g", "-j", str(self.runner.graceful)])
        self.wait(lambda: apps.app_running(str(self.runner.graceful)), "Background applet started")
        self.runner.observe()
        st = state.new_state()
        start = len(self.runner.commands)
        try:
            ok = Executor(Context(st)).execute(QuitApp(str(self.runner.graceful), self.runner.graceful.stem))
        except RuntimeError:
            if not tcc_denied(self.runner):
                raise
            print(TCC_SKIP)
            self.state_file.unlink(missing_ok=True)
            return False
        self.check(ok and not apps.app_running(str(self.runner.graceful)), "AppleEvent quit exited applet")
        self.check(
            not any(c[0] == "kill" for c in self.runner.commands[start:]),
            "Graceful quit used no kill fallback",
        )
        print("Reopen mapping: open <graceful bundle> -> open -g -j <graceful bundle>")
        self.off(st)
        self.wait(lambda: apps.app_running(str(self.runner.graceful)), "Applet reopened hidden in background")
        return True

    def thaw(self):
        rows = self.runner.observe()
        if any(
            r[0].startswith("T") and ".app/Contents/" in r[2] and pid not in self.runner.owned
            for pid, r in rows.items()
        ):
            print("SKIP thaw --all: foreign stopped .app process exists; never resume user processes")
            return
        st = state.new_state()
        self.check(Executor(Context(st)).execute(FreezeApp(str(self.apps[1]))), "Thaw fixture frozen")
        from travel_mode.cli import main

        self.check(main(["thaw", "--all"]) == 0, "Real thaw --all entrypoint succeeded")
        self.check(not apps.frozen_count(str(self.apps[1])), "Thaw resumed only fixture processes")
        self.off(st)
        self.assert_foreign()

    def power(self):
        # Capability observations are identical on a hosted VM and a maintainer Mac.
        # AC remains mandatory; missing battery settings never authorize AC writes.
        self.check(pmset.on_ac_power(), "AC connected; battery settings cannot affect current work")
        profiles = pmset.pmset_custom()
        if not profiles.get("b"):
            self.skip("power", "No Battery Power profile; battery-setting round trip unavailable")
            return
        permission = self.runner(
            ["sudo", "-n", "-l", "/usr/bin/pmset", "-a", "disablesleep", "0"], timeout=10
        )
        if permission.rc:
            self.skip("power", f"pmset-only sudo permission unavailable: {permission.stderr}")
            return
        self.original_power = self.command(["pmset", "-g", "custom"])
        self.original_sleep = pmset.sleep_disabled()
        self.check_power(pmset.on_ac_power(), "AC connected; battery settings cannot affect current work")
        profiles = pmset.pmset_custom()
        battery, ac = profiles.get("b", {}), profiles.get("a", {})
        mode = "lowpowermode" if "lowpowermode" in battery else "powermode"
        keys = ["displaysleep", "sleep", "powernap", mode, "hibernatemode"]
        self.check_power(
            all(k in battery for k in keys) and all(k in ac for k in ("powernap", "womp")),
            "All selected power keys have observed originals",
        )
        self.check_power(self.original_sleep in {"0", "1"}, "SleepDisabled original is valid")
        self.original_values = [part for key in keys for part in (key, battery[key])]
        self.rollback_commands = [
            ["sudo", "-n", "/usr/bin/pmset", "-b", *self.original_values],
            ["sudo", "-n", "/usr/bin/pmset", "-c", "powernap", ac["powernap"], "womp", ac["womp"]],
            ["sudo", "-n", "/usr/bin/pmset", "-a", "disablesleep", self.original_sleep],
        ]
        for command in self.rollback_commands:
            self.runner.validate(command)
            self.check_power(
                self.runner(["sudo", "-n", "-l", *command[2:]], timeout=10).rc == 0,
                "Emergency rollback permission available without a password",
            )
        self.record_power_originals()
        self.stage("power-before")
        st = state.new_state()
        st["power_origin"] = {
            "batt_prof": " ".join(self.original_values),
            "ac_prof": f"powernap {ac['powernap']} womp {ac['womp']}",
            "sleepdisabled": self.original_sleep,
        }
        self.power_dirty = True
        executor = Executor(Context(st))
        target = {"displaysleep": "2", "sleep": "2", "powernap": "0", mode: "1", "hibernatemode": "25"}
        for action in (
            EnableSleep(),
            SetPowerProfile([p for k, v in target.items() for p in (k, v)]),
            SetPowerProfile(["powernap", "0", "womp", "0"], "-c"),
        ):
            self.check_power(executor.execute(action)[1] == 0, "Product safe power action succeeded")
        self.stage("power-applied")
        changed = pmset.pmset_custom()
        self.check_profile(battery, changed["b"], target, "Battery")
        self.check_profile(ac, changed["a"], {"powernap": "0", "womp": "0"}, "AC")
        self.check_power(pmset.sleep_disabled() == "0", "Product repaired SleepDisabled")
        self.off(st)
        self.stage("power-off")
        self.verify_power()
        self.power_dirty = False

    def record_power_originals(self):
        print("ORIGINAL pmset -g custom:\n" + self.original_power)
        print("ORIGINAL SleepDisabled=" + self.original_sleep)
        for command in self.rollback_commands:
            print("EMERGENCY rollback argv: " + shlex.join(command))

    @contextmanager
    def brightness_capability(self):
        backend = None
        if self.brightness_loader is not None:
            try:
                native = self.brightness_loader()
                native.get()
                backend = ReadOnlyBrightness(native)
            except (OSError, AttributeError, RuntimeError) as error:
                print(f"SKIP native brightness capability: Get failed: {error}")
        # Preserve genuine readable capability in the UI without invoking a CLI
        # command or exposing a setter during slow phases.
        with (
            patch.object(brightness, "get_runner", return_value=shell.subprocess_runner),
            patch.object(brightness, "select_backend", return_value=backend),
        ):
            yield

    def verify_power(self):
        self.check_power(
            self.command(["pmset", "-g", "custom"]) == self.original_power,
            "Full pmset output restored byte for byte",
        )
        self.check_power(
            pmset.sleep_disabled() == self.original_sleep, "SleepDisabled restored to captured original"
        )

    def brightness(self):
        if self.brightness_loader is None:
            self.brightness_report = {"status": "SKIP", "reason": "no explicit native backend supplied"}
            print("SKIP brightness: no explicit native backend supplied")
            return
        try:
            native = self.brightness_loader()
            original = native.get()
        except (OSError, AttributeError, RuntimeError) as error:
            self.brightness_report = {"status": "SKIP", "reason": f"Native Get failed: {error}"}
            print("SKIP brightness: " + self.brightness_report["reason"])
            return
        if not isinstance(original, (float, int)) or not 0 <= original <= 1:
            raise ValueError(f"Native Get returned invalid brightness: {original!r}")
        self.brightness_boundary = RestrictedBrightness(native, original)
        self.brightness_report = {"status": "FAIL", "original": original, "target": 0.15}
        if self.state_file.exists():
            raise RuntimeError("Brightness round trip requires completed off and no existing state")
        st = state.new_state()
        # Product brightness normally routes custom Runners to a CLI double.
        # Scope only this module to its real native path; all other commands
        # retain GuardRunner. No native mutation occurs outside this interface.
        with (
            patch.object(brightness, "get_runner", return_value=shell.subprocess_runner),
            patch.object(brightness, "select_backend", return_value=self.brightness_boundary),
        ):
            try:
                self.brightness_dirty = True  # A setter failure can still have changed the display.
                started = time.monotonic()
                _, rc = Executor(Context(st)).execute(SetBrightness(0.15))
                if rc:
                    raise RuntimeError(f"Product SetBrightness failed: rc={rc}")
                changed = native.get()
                self.brightness_report["observed_target"] = changed
                self.check_brightness(
                    abs(changed - 0.15) <= 0.02, "Brightness target within 0.02 of 0.15", observed=changed
                )
                self.stage("brightness-applied")
                self.off(st)  # Brightness-only receipt: no slow app or power work while dimmed.
                restored = native.get()
                self.brightness_report["observed_restored"] = restored
                self.check_brightness(
                    abs(restored - original) <= 0.01,
                    "Brightness off restored within 0.01",
                    observed=restored,
                )
                self.brightness_dirty = False
                self.brightness_report.update(status="PASS", dim_window_seconds=time.monotonic() - started)
            finally:
                self.restore_brightness()
        self.brightness_report["native_set_values"] = self.brightness_boundary.calls
        print("BRIGHTNESS: " + repr(self.brightness_report))
        print(
            "NOT TESTED: Wi-Fi/networksetup (network disruption); Bluetooth (blueutil absent); "
            "Spotlight/mdutil (sudo outside pmset); caffeinate (user processes excluded from e2e); "
            "displaysleepnow (interrupts work); AC sleep/displaysleep (interrupts work)."
        )

    def restore_brightness(self):
        if self.brightness_dirty:
            boundary = self.brightness_boundary
            _, rc = boundary.set(boundary.original)
            if rc:
                raise RuntimeError(f"Emergency native brightness restore failed: rc={rc}")
            restored = boundary.get()
            self.brightness_report["emergency_restored"] = restored
            self.check_brightness(
                abs(restored - boundary.original) <= 0.01,
                "Emergency native brightness restored",
                observed=restored,
            )
            self.brightness_dirty = False

    def cleanup(self):
        errors = []

        def attempt(operation):
            try:
                operation()
            except (OSError, RuntimeError, ValueError, AssertionError) as error:
                # Continue independent cleanup, then propagate every original failure.
                errors.append(error)
                print(f"CLEANUP ERROR: {error}", file=sys.stderr)

        def rollback():
            if self.power_dirty:
                with ExitStack() as rollbacks:
                    for command in reversed(self.rollback_commands):
                        rollbacks.callback(attempt, lambda command=command: self.command(command))
                self.verify_power()
                print("EMERGENCY POWER ROLLBACK verified byte for byte")
                self.power_dirty = False

        def signal_current(signal):
            rows = self.runner.observe()
            # ExitStack still attempts the other fixture PIDs on unexpected errors.
            with ExitStack() as signals:
                for pid, row in reversed(list(rows.items())):
                    if (
                        pid in self.runner.owned
                        and (row[1], row[2]) == self.runner.owned[pid]
                        and not row[0].startswith("Z")
                    ):
                        signals.callback(attempt, lambda pid=pid: self.command(["kill", signal, str(pid)]))

        def terminate():
            if self.fixture_attempted:
                # Also run TERM if CONT fails unexpectedly.
                with ExitStack() as signals:
                    signals.callback(attempt, lambda: signal_current("-TERM"))
                    signals.callback(attempt, lambda: signal_current("-CONT"))

        def kill_survivors():
            if self.fixture_attempted:
                self.pause(1.1)
                signal_current("-KILL")

        def unload():
            if self.agent_attempted:
                if self.agent_loaded():
                    self.command(["launchctl", "unload", str(self.plist)])
                self.check(not self.agent_loaded(), "Cleanup removed fake agent")

        def verify_process_cleanup():
            if self.fixture_attempted:
                rows = self.runner.observe()
                self.check(
                    not any(
                        row[2] in self.runner.shells and not row[0].startswith("Z") for row in rows.values()
                    ),
                    "Cleanup terminated every fake interpreter",
                )

        def quit_applet():
            if self.graceful_attempted and apps.app_running(str(self.runner.graceful)):
                result = self.runner(QUIT_EVENT, timeout=10)
                if result.rc and not tcc_denied(self.runner):
                    raise RuntimeError(f"Cleanup AppleEvent quit failed: {result}")
                if result.rc:
                    print(TCC_SKIP)
                else:
                    self.pause(1)

        def unregister():
            if self.graceful_attempted and self.runner.graceful.exists():
                self.command([LSREGISTER, "-u", str(self.runner.graceful)])

        # LIFO callbacks guarantee independent cleanup even for unexpected exception
        # types. Expected failures are collected and re-raised with their causes.
        with ExitStack() as phases:
            phases.callback(attempt, lambda: shutil.rmtree(self.root))
            if self.before is not None:
                phases.callback(attempt, self.assert_foreign)
            phases.callback(attempt, unregister)
            phases.callback(attempt, verify_process_cleanup)
            if hasattr(self.runner.backend, "reap"):
                phases.callback(attempt, self.runner.backend.reap)
            phases.callback(attempt, unload)
            phases.callback(attempt, kill_survivors)
            phases.callback(attempt, terminate)
            phases.callback(attempt, quit_applet)
            phases.callback(attempt, rollback)
            phases.callback(attempt, self.restore_brightness)
        if errors:
            raise CleanupError(errors) from errors[0]
        print("PASS: Cleanup complete")

    def run(self):
        preflight(self.root, self.state_file, self.real_state)
        with product_environment(self.root, self.runner), self.brightness_capability():
            try:
                self.sandbox()
                self.graceful()
                self.thaw()
                self.power()
                self.brightness()
            finally:
                self.cleanup()
        self.check(not self.runner.tripped, "No guard violations")
        print("PASS: local e2e (see SKIP lines for unavailable power stage)")


TCC_SKIP = (
    "SKIP graceful: AppleEvent denied (-1743) or timed out; allow automation in "
    "System Settings > Privacy & Security > Automation. Product real use has "
    "the same first-authorization requirement."
)


def tcc_denied(runner):
    return bool(
        runner.results
        and runner.results[-1][0] == tuple(QUIT_EVENT)
        and (
            runner.results[-1][1].rc == 124
            or any(
                code in runner.results[-1][1].stderr + runner.results[-1][1].stdout
                for code in ("-1743", "-1712")
            )
        )
    )


def create_graceful(root, runner):
    app = runner.graceful
    result = runner(
        ["osacompile", "-s", "-o", str(app), "-e", "on idle", "-e", "return 1", "-e", "end idle"], timeout=30
    )
    if result.rc:
        raise RuntimeError(f"osacompile failed: {result}")
    plist = app / "Contents/Info.plist"
    metadata = plistlib.loads(plist.read_bytes())
    metadata.update(CFBundleIdentifier=GRACEFUL_ID, LSUIElement=True)
    plist.write_bytes(plistlib.dumps(metadata))
    result = runner(["codesign", "--force", "--sign", "-", str(app)], timeout=30)
    if result.rc:
        raise RuntimeError(f"Applet ad-hoc signing failed: {result}")


def print_plan():
    print("PLAN ONLY: no system commands, fixture processes or filesystem writes.")
    print("Preflight: no real state; dedicated temporary root; agent label absent; AC connected.")
    print("Isolate HOME/XDG/config/state and app/agent discovery; snapshot foreign process states.")
    print("Disable built-in defaults/categories; restrict discovery to fixture bundles and agent.")
    print("Keep unfiltered GuardRunner process observations for identity checks and foreign-process safety.")
    root = Path("/tmp/travel-mode-e2e-PLAN")
    guard = GuardRunner(None, root, fixture_paths(root), root / "agent.plist", plan=True)
    for cmd in [
        PROCESS_QUERY,
        STATUS_QUERY,
        ["launchctl", "list"],
        ["osacompile", "-s", "-o", str(guard.graceful), "-e", "on idle", "-e", "return 1", "-e", "end idle"],
        ["codesign", "--force", "--sign", "-", str(guard.graceful)],
        ["open", "-g", "-j", str(guard.graceful)],
        QUIT_EVENT,
        ["open", str(guard.graceful)],
    ]:
        guard(cmd)
    print(
        "plistlib: set CFBundleIdentifier=com.example.travelmode-e2e.graceful, "
        "LSUIElement=true before signing."
    )
    print("open <graceful bundle> is mapped to open -g -j <bundle>; no focus/Dock activation.")
    print("Assert AppleEvent exits applet; recorded commands contain no applet kill; off reopens it.")
    print(TCC_SKIP)
    for exe in guard.executables:
        guard([exe])
    guard(["launchctl", "load", str(guard.plist)])
    print("Product ForceQuitApp/QuitApp, FreezeApp, UnloadAgent; exact fixture receipts; off restores all.")
    guard.owned[900001] = ("<birth>", guard.shells[0])
    for signal in ("-TERM", "-STOP", "-CONT"):
        guard(PROCESS_QUERY)
        guard(["kill", signal, "900001"])
    print("thaw --all: SKIP if any foreign stopped .app; otherwise freeze fixture, CLI thaw, assert resumed.")
    print(
        "Capture complete pmset -g custom and SleepDisabled; validate ALL rollback "
        "argv and sudo -n -l permissions BEFORE changes."
    )
    guard(["pmset", "-g", "custom"])
    guard(["pmset", "-g"])
    print("Power values below are explicit examples; real runs capture originals, never synthesize them.")
    rollbacks = [
        [
            "/usr/bin/pmset",
            "-b",
            "displaysleep",
            "10",
            "sleep",
            "15",
            "powernap",
            "0",
            "lowpowermode",
            "0",
            "hibernatemode",
            "3",
        ],
        ["/usr/bin/pmset", "-c", "powernap", "1", "womp", "1"],
        ["/usr/bin/pmset", "-a", "disablesleep", "1"],
    ]
    print("Before any write: validate every rollback argv; probe its exact unattended permission:")
    for cmd in rollbacks:
        guard.validate(["sudo", "-n", *cmd])
        guard(["sudo", "-n", "-l", *cmd])
    print("If initial pmset permission is unavailable: SKIP power. If AC is absent: abort before writes.")
    for cmd in [
        ["/usr/bin/pmset", "-a", "disablesleep", "0"],
        ["/usr/bin/pmset", "-c", "powernap", "0", "womp", "0"],
        [
            "/usr/bin/pmset",
            "-b",
            "displaysleep",
            "2",
            "sleep",
            "2",
            "powernap",
            "0",
            "lowpowermode",
            "1",
            "hibernatemode",
            "25",
        ],
    ]:
        guard(["sudo", "-n", "-l", *cmd])
        guard(["sudo", "-n", *cmd])
    print(
        "on assertions: compare each selected target, unchanged unselected battery/AC keys "
        "(SleepServices may follow powernap only when that profile's powernap changes), "
        "state receipts and foreign processes:"
    )
    guard(["pmset", "-g", "custom"])
    guard(["pmset", "-g"])
    guard(PROCESS_QUERY)
    guard(STATUS_QUERY)
    guard(["launchctl", "unload", str(guard.plist)])
    guard(["launchctl", "list"])
    print("off: restore only recorded keys; same commands also cover emergency rollback on any failure:")
    for cmd in rollbacks:
        guard(["sudo", "-n", *cmd])
    guard(["pmset", "-g", "custom"])
    guard(["pmset", "-g"])
    guard(["launchctl", "load", str(guard.plist)])
    guard(["ps", "-axo", "pid,stat,comm"])
    print("powermode replaces lowpowermode when advertised. off restores every captured value.")
    print(
        "finally emergency rollback: -b captured keys/values; -c powernap+womp "
        "originals; -a disablesleep original; verify full output and SleepDisabled."
    )
    print(
        "Brightness: explicit native DisplayServices Get original; Get failure => SKIP with reason. "
        "Product SetBrightness(0.15); native Get assert abs(value-0.15)<=0.02; "
        "immediate brightness-only off; native Get assert abs(value-original)<=0.01."
    )
    print(
        "Native calls bypass subprocess guard: restricted interface permits only 0.15 and captured original."
    )
    print(
        "Brightness finally/emergency: direct product native backend set(original), Get verify <=0.01; "
        "before all other slow cleanup. No CLI fallback and no other work during the dim window."
    )
    print(
        "NOT TESTED: Wi-Fi/networksetup, Bluetooth, Spotlight/mdutil, caffeinate, "
        "displaysleepnow, AC sleep/displaysleep."
    )
    print("finally: graceful quit then identity-checked CONT/TERM/KILL survivors, unload only owned agent;")
    guard(QUIT_EVENT)
    for signal in ("-CONT", "-TERM", "-KILL"):
        guard(PROCESS_QUERY)
        guard(["kill", signal, "900001"])
    guard(["launchctl", "unload", str(guard.plist)])
    guard(["launchctl", "list"])
    guard([LSREGISTER, "-u", str(guard.graceful)])
    print(
        "Reap children; assert no foreign stop/resume; remove temporary root. Fail loudly on cleanup errors."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="Print guarded command branches; execute nothing")
    parser.add_argument(
        "--output", type=Path, help="Persistent JSON evidence directory (outside fixture root)"
    )
    args = parser.parse_args(argv)
    if args.plan:
        print_plan()
        return 0
    real_state = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/travel-mode/state.json"
    if real_state.exists():
        print(f"FAIL: Active user state exists: {real_state}", file=sys.stderr)
        return 1
    root = Path(tempfile.mkdtemp(prefix="travel-mode-e2e-")).resolve()
    paths = fixture_paths(root)
    executables = [str(app / "Contents/MacOS/loop") for app in paths]
    runner = GuardRunner(LocalBackend(executables), root, paths, root / "agent.plist")
    evidence = args.output or Path("/tmp/travel-mode-e2e-evidence") / datetime.now().strftime(
        "%Y%m%d-%H%M%S-%f"
    )
    evidence.mkdir(parents=True, exist_ok=False)
    harness = Harness(root, runner, real_state, brightness_loader=brightness.NativeBrightness.load)
    report = dict(status="FAIL")
    try:
        harness.run()
        report["status"] = "PASS"
    except (OSError, RuntimeError, ValueError, AssertionError, KeyboardInterrupt) as error:
        report["error"] = f"{type(error).__name__}: {error}"
        print(f"FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        print(f"FAIL: unexpected {type(error).__name__}: {error}", file=sys.stderr)
        raise RuntimeError(
            "Unexpected local e2e failure; see the original cause and cleanup output"
        ) from error
    finally:
        report.update(
            skips=harness.skips,
            brightness=harness.brightness_report,
            guard_tripped=runner.tripped,
            violations=runner.violations,
            original_power=harness.original_power,
            original_sleepdisabled=harness.original_sleep,
        )
        (evidence / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        (evidence / "command_trace.json").write_text(json.dumps(runner.trace, indent=2) + "\n")
        print(f"Evidence: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

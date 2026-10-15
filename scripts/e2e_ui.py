#!/usr/bin/env python
"""Guarded interactive CLI/TUI round trips; --demo has no host command backend."""

from __future__ import annotations

import argparse
import builtins
import json
import os
import pwd
import re
import shlex
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_fake import Machine, SimulatedBrightness
from e2e_guard import LABEL, GuardRunner, LocalBackend
from e2e_local import Harness, create_fixtures, fixture_paths, preflight, print_plan, product_environment
from terminal_render import render
from tui_debug import ROOT, DebugApp, Session, isolated_env

from travel_mode import state
from travel_mode.cli import main as product_main
from travel_mode.i18n import set_language, t
from travel_mode.system import apps, brightness, pmset, processes


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def wait_for(predicate, message, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise TimeoutError(message)


class InteractiveHarness(Harness):
    def setup(self):
        if self.agent_loaded():
            raise RuntimeError("Fixture agent already loaded")
        self.fixture_attempted = True
        create_fixtures(self.root)
        for executable in self.runner.executables:
            self.command([executable])
        self.wait(lambda: len(self.runner.observe()) >= 4, "Shell fixtures started")
        self.agent_attempted = True
        self.command(["launchctl", "load", str(self.plist)])
        self.wait(self.agent_loaded, "Agent loaded")
        # This proves TCC capability independently. Denied authorization skips
        # this fixture only; the remaining CLI/TUI phases continue.
        graceful_ready = self.graceful()
        if self.state_file.exists():
            raise AssertionError("Preflight graceful stage left a state file")
        config = self.root / "config.toml"
        config.write_text(
            '[apps]\ndefault="skip"\nquit='
            + ('["TravelModeE2E-Graceful"]' if graceful_ready else "[]")
            + '\nfreeze=["TravelModeE2E-Freeze"]\nskip=["TravelModeE2E-Quit"]\n'
            '[services]\nunload=["' + LABEL + '"]\n[network]\napps=[]\n'
            '[options]\nlanguage="en"\n'
        )
        print(
            "SKIP shell Quit fixture in UI; covered by local ForceQuitApp: "
            "its AppleEvent is outside the exact guard allowlist.",
            flush=True,
        )
        self.runner.observe()
        self.before = self.foreign_snapshot()
        self.original_power = self.command(["pmset", "-g", "custom"])
        self.original_sleep = pmset.sleep_disabled()
        self.check_power(pmset.on_ac_power(), "AC required throughout UI tests")
        profiles = pmset.pmset_custom()
        self.rollback_commands = []
        for domain, profile, keys in [("-b", "b", list(profiles["b"])), ("-c", "a", ["powernap", "womp"])]:
            values = [p for k in keys for p in (k, profiles[profile][k])]
            command = ["sudo", "-n", "/usr/bin/pmset", domain, *values]
            # Only selected/supported battery recommendation keys are testable.
            if domain == "-b":
                selected = {k for k, _, _ in pmset.power_recommendations()} | {"hibernatemode"}
                command = [
                    "sudo",
                    "-n",
                    "/usr/bin/pmset",
                    domain,
                    *[p for k in keys if k in selected for p in (k, profiles[profile][k])],
                ]
            self.rollback_commands.append(command)
        self.rollback_commands.append(
            ["sudo", "-n", "/usr/bin/pmset", "-a", "disablesleep", self.original_sleep]
        )
        for command in self.rollback_commands:
            self.runner.validate(command)
            self.check_power(
                self.runner(["sudo", "-n", "-l", *command[2:]]).rc == 0, "Unattended rollback authorized"
            )
        self.record_power_originals()
        self.power_dirty = True
        self.original_profiles = profiles
        self.freeze_pids = {
            p: r[1:] for p, r in self.runner.observe().items() if r[2].startswith(str(self.apps[1]) + "/")
        }
        self.action_start = len(self.runner.commands)
        self.graceful_ready = graceful_ready

    def assert_on(self):
        self.runner.observe()
        self.check(apps.frozen_count(str(self.apps[1])) == 2, "UI froze fixture main/helper")
        self.check(not self.agent_loaded(), "UI unloaded fixture agent")
        profiles = pmset.pmset_custom()
        self.check_power(pmset.sleep_disabled() == "0", "UI repaired SleepDisabled")
        self.check_profile(
            self.original_profiles["a"], profiles["a"], {"powernap": "0", "womp": "0"}, "UI AC"
        )
        targets = {key: target for key, _, target in pmset.power_recommendations()}
        targets["hibernatemode"] = "25"
        self.check_profile(self.original_profiles["b"], profiles["b"], targets, "UI battery")
        applet_pids = {
            pid
            for pid, (_, command) in self.runner.owned.items()
            if command.startswith(str(self.runner.graceful) + "/")
        }
        self.check(
            not any(
                c[0] == "kill" and int(c[-1]) in applet_pids
                for c in self.runner.commands[self.action_start :]
            ),
            "UI graceful applet used no kill fallback",
        )
        if self.graceful_ready:
            self.check(not apps.app_running(str(self.runner.graceful)), "UI gracefully quit fixture")
        self.check(self.state_file.exists(), "UI persisted isolated state")
        saved = state.load_state()
        self.check(
            len(saved["frozen_pids"]) == 2 and len(saved["unloaded_svcs"]) == 1, "UI exact fixture receipts"
        )
        self.assert_foreign()
        self.check(not self.runner.tripped, "UI no guard violations")

    def assert_off(self):
        self.check(not self.state_file.exists(), "UI off removed isolated state")
        self.check(self.agent_loaded(), "UI off reloaded agent")
        self.check(not apps.frozen_count(str(self.apps[1])), "UI off resumed fixture")
        rows = self.runner.observe()
        self.check(
            all(
                pid in rows and rows[pid][1:] == identity and not rows[pid][0].startswith("T")
                for pid, identity in self.freeze_pids.items()
            ),
            "UI off resumed the same fixture identities",
        )
        if self.graceful_ready:
            self.wait(lambda: apps.app_running(str(self.runner.graceful)), "UI off reopened applet")
        self.verify_power()
        self.power_dirty = False
        self.assert_foreign()
        self.check(not self.runner.tripped, "UI round trip no guard violation")


def answers():
    """Exact i18n-rendered prompt allowlist, derived from the observed snapshot."""
    yes = [
        "impl.fix_pmset_disablesleep",
        "impl.use_hibernatemode_write_ram_to_disk",
        "impl.reduce_ac_activity_too_disable_powernap",
    ]
    no = [
        "impl.reduce_brightness_to_off_restores_the",
        "impl.stop_spotlight_indexing_mdutil_i_off",
        "impl.terminate_caffeinate_a_process_with_t",
        "impl.turn_off_the_display_now_pmset",
        "impl.quit_the_configured_daemon_app",
    ]
    result = {}
    for key, answer in [(k, "y") for k in yes] + [(k, "n") for k in no]:
        result[t(key)] = (key, answer)
    for target in processes.caffeinate_procs():
        result[t("caffeinate.confirm", pid=target.process.pid)] = (
            f"skip-caffeinate-{target.process.pid}",
            "n",
        )
    from travel_mode.network_apps import configured_names

    result[t("impl.disconnect_completely_quit_configured_network_apps", v0=", ".join(configured_names()))] = (
        "disconnect",
        "n",
    )
    profiles = pmset.pmset_custom()
    for k, desc, rec in pmset.power_recommendations():
        prompt = t(
            "impl.set_current_recommended", v0=k, v1=rec, v2=profiles["b"].get(k, "(unset)"), v3=rec, v4=desc
        )
        result[prompt] = ("battery-" + k, "y")
    for row in apps.scan_apps()["running"]:
        default = row["defact"] if row["defact"] != "ask" else "quit"
        prompt = t(
            "impl.quit_freeze_skip",
            v0=f"{row['name']} (CPU <CPU>%)",
            v1="Y" if default == "quit" else "y",
            v2="F" if default == "freeze" else "f",
        )
        result[cpu_pattern(prompt)] = (row["name"], "f" if default == "freeze" else "y")
    # Render the service prompt through the same catalogue as the product.
    from travel_mode.system import launchd

    for row in launchd.svc_scan()["unloadable"]:
        result[
            cpu_pattern(t("impl.unload_agent_prompt", label=row["label"], pid=row["pid"], cpu="<CPU>"))
        ] = (
            "fixture-agent",
            "y",
        )
    return result


def cpu_pattern(prompt):
    # Only this numerical observation may vary between the policy snapshot and
    # the product prompt. Fixture identity and all rendered wording stay exact.
    return re.compile(re.escape(prompt).replace("<CPU>", r"(?:[0-9]+(?:\.[0-9]+)?|\?)"))


def select_answer(prompt, policy):
    matches = []
    for expected, answer in policy.items():
        if isinstance(expected, re.Pattern):
            variants = [prompt]
            for risk in ("", "⚠ "):
                for hint in ("[Y/n]", "[y/N]"):
                    prefix, suffix = f"  ? {risk}", f" {hint} "
                    if prompt.startswith(prefix) and prompt.endswith(suffix):
                        variants.append(prompt[len(prefix) : -len(suffix)])
            if any(expected.fullmatch(value) for value in variants):
                matches.append(answer)
        elif prompt == expected or prompt in [
            f"  ? {risk}{expected} {hint} " for risk in ("", "⚠ ") for hint in ("[Y/n]", "[y/N]")
        ]:
            matches.append(answer)
    if len(matches) != 1:
        raise AssertionError(f"Unexpected or ambiguous product prompt: {prompt!r}")
    return matches[0]


def child(args):
    root = args.root.resolve()
    backend = (
        Machine(root)
        if args.demo
        else LocalBackend([str(a / "Contents/MacOS/loop") for a in fixture_paths(root)])
    )
    runner = GuardRunner(backend, root, fixture_paths(root), root / "agent.plist")
    harness = InteractiveHarness(
        root,
        runner,
        Path(args.real_state),
        brightness_loader=SimulatedBrightness if args.demo else brightness.NativeBrightness.load,
    )
    report = {"mode": "demo" if args.demo else "real", "interface": args.child, "status": "FAIL"}
    # Evidence survives temporary fixture cleanup.
    evidence = args.evidence
    try:
        with product_environment(root, runner), harness.brightness_capability():
            try:
                preflight(root, harness.state_file, harness.real_state)
                set_language("en")
                harness.setup()
                if args.child == "cli":
                    policy = answers()
                    original_input = builtins.input
                    count = 0

                    def guarded_input(prompt):
                        nonlocal count
                        # ask() adds only the bullet, risk marker and yes/no hint.
                        step, answer = select_answer(prompt, policy)
                        count += 1
                        write_json(
                            evidence / "prompt.json",
                            {"count": count, "prompt": prompt, "key": step, "answer": answer},
                        )
                        return original_input(prompt)

                    builtins.input = guarded_input
                    try:
                        harness.check_power(
                            product_main(["--no-tui", "--lang", "en"]) == 0, "CLI apply succeeded"
                        )
                    finally:
                        builtins.input = original_input
                    harness.assert_on()
                    write_json(evidence / "on.json", {"status": "PASS"})
                    original_input("E2E: on assertions passed; press Enter for off ")
                    harness.check_power(product_main(["off", "--lang", "en"]) == 0, "CLI off succeeded")
                else:

                    class ObservedApp(DebugApp):
                        def on_descendant_focus(self, event):
                            self.call_after_refresh(self.write_layout)

                        def write_layout(self):
                            result = super().write_layout()
                            layout_path = evidence / "layout.json"
                            if result:
                                layout = json.loads(layout_path.read_text())
                                layout.update(
                                    action_generation=self._mutation_generation,
                                    mutation_busy=self._mutation_lock.locked(),
                                    focused_id=self.focused.id if self.focused else None,
                                )
                                write_json(layout_path, layout)
                            request = evidence / "assert-on.request"
                            if (
                                request.exists()
                                and not (evidence / "on.json").exists()
                                and not self._mutation_lock.locked()
                                and self.refresh_complete
                            ):
                                harness.assert_on()
                                write_json(evidence / "on.json", {"status": "PASS"})
                            return result

                    ObservedApp(evidence / "layout.json").run()
                harness.assert_off()
                harness.brightness()
            finally:
                harness.cleanup()
        report["status"] = "PASS"
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["guard_tripped"] = runner.tripped
        report["violations"] = runner.violations
        report["commands"] = [list(c) for c in runner.commands]
        report["original_power"] = harness.original_power
        report["original_sleepdisabled"] = harness.original_sleep
        report["emergency_rollback"] = harness.rollback_commands
        report["brightness"] = harness.brightness_report
        write_json(evidence / "result.json", report)
    return 0


class UISession(Session):
    def __init__(self, evidence, fixture, interface, demo):
        self.root = evidence
        self.cols = 160
        self.rows = 55
        self.language = "en"
        self.timeout = 40
        self.config_source = {"kind": "isolated-fixtures"}
        self.env = isolated_env(evidence)
        self.socket = str(evidence / "tmux.socket")
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv required")
        command = [
            uv,
            "run",
            "--project",
            str(ROOT),
            "python",
            str(Path(__file__).resolve()),
            "--child",
            interface,
            "--root",
            str(fixture),
            "--evidence",
            str(evidence),
            "--real-state",
            str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/travel-mode/state.json"),
        ]
        if demo:
            command.append("--demo")
        self.tmux(
            "new-session", "-d", "-x", str(self.cols), "-y", str(self.rows), "-s", "e2e", shlex.join(command)
        )
        self.tmux("set-option", "remain-on-exit", "on")
        self.tmux("set-option", "mouse", "on")
        self.captures = []

    def shot(self, name):
        plain = self.tmux("capture-pane", "-p")
        ansi = self.tmux("capture-pane", "-p", "-e")
        (self.root / (name + ".txt")).write_text(plain)
        render(ansi, self.root / (name + ".png"), self.cols, self.rows)
        self.captures.append({"step": name, "screenshot": str(self.root / (name + ".png"))})

    def prompt(self, event):
        wanted = " ".join(event["prompt"].split())
        wait_for(
            lambda: wanted in " ".join(self.tmux("capture-pane", "-p", "-J").split()),
            f"Missing expected prompt: {wanted}",
            self.timeout,
        )
        self.shot(f"{event['count']:02d}-{event['key']}")
        self.tmux("send-keys", event["answer"], "Enter")

    def finish(self):
        result = wait_for(
            lambda: (
                json.loads((self.root / "result.json").read_text())
                if (self.root / "result.json").exists()
                else None
            ),
            "Child did not finish",
            self.timeout,
        )
        self.shot("finished")
        if result["status"] != "PASS" or result["guard_tripped"]:
            raise AssertionError(f"Child failed: {self.root}/result.json")


def drive_cli(session):
    count = 0
    while not (session.root / "on.json").exists():

        def next_event(count=count):
            path = session.root / "prompt.json"
            if (session.root / "result.json").exists():
                raise AssertionError("CLI failed before on assertion")
            if (session.root / "on.json").exists():
                return "on"
            if path.exists():
                event = json.loads(path.read_text())
                if event["count"] > count:
                    return event

        event = wait_for(next_event, "CLI prompt did not arrive", session.timeout)
        if event == "on":
            break
        session.prompt(event)
        count = event["count"]
    session.shot("on-verified")
    session.tmux("send-keys", "Enter")
    session.finish()


def mutate(session, widget, step):
    path = session.root / "layout.json"
    generation = json.loads(path.read_text())["action_generation"]
    if widget == "b-power":
        # Keyboard focus scrolls the bottom button into view on long detail pages.
        for _ in range(80):
            if json.loads(path.read_text()).get("focused_id") == widget:
                break
            session.keys("Tab")
        else:
            raise AssertionError(f"Keyboard focus did not reach {widget}")
        session.shot(step + "-focused")
        session.keys("Enter")
    else:
        session.widget_click(widget)

    def completed():
        layout = json.loads(path.read_text())
        if (session.root / "result.json").exists():
            raise AssertionError(f"TUI exited while executing {widget}")
        return layout["action_generation"] > generation and not layout["mutation_busy"] and layout["ready"]

    wait_for(completed, f"TUI action {widget} did not complete", session.timeout)
    session.shot(step)


def drive_tui(session):
    session.wait_started()
    session.wait_complete()
    session.shot("01-dashboard")
    # Fix SleepDisabled through its real button. No unsafe diagnostic buttons.
    mutate(session, "b-fixsleep", "02-fixsleep")
    session.keys("2")
    session.wait_complete()
    session.widget_click("cb-hib25")
    session.widget_click("cb-ac")
    session.shot("03-power-selection")
    mutate(session, "b-power", "04-power-applied")
    session.keys("3")
    session.wait_complete()
    session.shot("05-apps")
    mutate(session, "b-apps-all", "06-apps-applied")
    session.keys("4")
    session.wait_complete()
    session.shot("07-services")
    mutate(session, "b-svc-all", "08-services-applied")
    (session.root / "assert-on.request").touch()
    # A harmless refresh provides a rendered completion point for the assertion.
    session.keys("r")
    wait_for(lambda: (session.root / "on.json").exists(), "TUI on assertions did not finish", session.timeout)
    session.shot("09-on-verified")
    session.keys("5")
    session.wait_complete()
    session.shot("10-network-restore")
    mutate(session, "b-off", "11-off-restored")
    session.keys("q")
    session.finish()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--interface", choices=("both", "cli", "tui"), default="both")
    parser.add_argument("--child", choices=["cli", "tui"], help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--evidence", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--real-state", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.plan:
        print_plan()
        print("UI: two isolated tmux children install GuardRunner BEFORE entering real CLI/TravelApp.")
        print(
            "CLI: match exact i18n-rendered prompt before each send-keys; yes only safe "
            "power/fixture actions; no all other prompts."
        )
        print(
            "TUI: keyboard tabs; mouse fixsleep, hib25+AC, power apply, fixture "
            "defaults, agent unload; Network/Restore off."
        )
        print(
            "Every step: terminal_render PNG + text under /tmp/tui-e2e/<run-id>; "
            "timeout => screenshot + FAIL."
        )
        print(
            "Assert on/off fixture processes, launchctl, complete pmset originals, "
            "state creation/deletion, no guard violations, no foreign stop/resume."
        )
        print(
            "SKIP shell Quit in UI by product decision: quit target is graceful applet; "
            "local ForceQuitApp covers shell fixture. Exact osascript allowlist unchanged."
        )
        print(
            "Select standalone hib25 checkbox with original=recommendation=3; "
            "assert battery hibernatemode=25. No config override."
        )
        print("--demo uses shared stateful Machine backend; no real fixture launch/signals/power commands.")
        return 0
    if args.child:
        return child(args)
    output = args.output or Path("/tmp/tui-e2e") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for interface, driver in [("cli", drive_cli), ("tui", drive_tui)]:
        if args.interface not in ("both", interface):
            continue
        evidence = output / interface
        evidence.mkdir()
        fixture = Path(tempfile.mkdtemp(prefix="travel-mode-e2e-ui-"))
        session = UISession(evidence, fixture, interface, args.demo)
        try:
            driver(session)
        except (RuntimeError, TimeoutError, AssertionError, OSError):
            session.shot("failure")
            if not (evidence / "result.json").exists():
                session.tmux("send-keys", "q" if interface == "tui" else "C-c")
                wait_for(
                    lambda evidence=evidence: (evidence / "result.json").exists(),
                    "Interrupted child did not finish fixture cleanup",
                    session.timeout,
                )
            raise
        finally:
            rows.extend(session.captures)
            write_json(output / "screenshots.json", rows)
            # No kill-server while the child may own live fixtures: allow its
            # try/finally cleanup to finish before destroying the dedicated server.
            if (evidence / "result.json").exists():
                session.close()
    print("| Step | Screenshot |\n| --- | --- |")
    for row in rows:
        print(f"| {row['step']} | {row['screenshot']} |")
    print(f"PASS: UI round trips ({'demo' if args.demo else 'real'}); {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

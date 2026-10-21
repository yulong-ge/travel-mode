"""Maintainer-only event delivery check; --plan never loads an agent or posts events."""

from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import sys
import time
from pathlib import Path
from unittest.mock import patch

from harness_evidence import write_json

from travel_mode import automation, cli
from travel_mode.actions.automation import InstallAutomation, UninstallAutomation
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.system import shell

TEST_LABEL = "io.github.yulong-ge.travel-mode.events-e2e"


class GuardViolation(RuntimeError):
    pass


class EventGuard:
    def __init__(self, backend, path, helper, *, child=False, uid=None):
        self.backend, self.path, self.helper = backend, path, helper
        self.uid = os.getuid() if uid is None else uid
        self.child = child
        self.calls = []

    def __call__(self, command, timeout=None, interactive=False):
        allowed = [["launchctl", "print", f"gui/{self.uid}/{TEST_LABEL}"], ["pmset", "-g", "ps"]]
        if not self.child:
            allowed.extend(
                [
                    ["launchctl", "bootstrap", f"gui/{self.uid}", str(self.path)],
                    ["launchctl", "bootout", f"gui/{self.uid}/{TEST_LABEL}"],
                ]
            )
        if interactive or command not in allowed:
            raise GuardViolation("Rejected event e2e command: " + shlex.join(command))
        if command[1] == "bootstrap":
            data = plistlib.loads(self.path.read_bytes())
            if data != automation.agent_plist(str(self.helper)) or data["Label"] != TEST_LABEL:
                raise GuardViolation("Rejected event e2e plist")
        self.calls.append(list(command))
        return self.backend(command, timeout=timeout, interactive=False)


def plan():
    root = Path("/tmp/travel-mode-event-e2e-PLAN")
    path = Path.home() / "Library/LaunchAgents" / (TEST_LABEL + ".plist")
    helper = root / "event-helper"
    print("Maintainer entry: uv run python scripts/e2e_automation.py")
    print("The test uses an independent label and a temporary guarded Python helper.")
    print('1. Create isolated config with [automation] on_battery="off", on_ac="off"; no snapshot.')
    print("2. Create executable helper with this exact interpreter: " + sys.executable)
    print(
        "   Helper consumes one XPC event; only pmset -g ps is allowed. "
        "Notifications and all writes are rejected."
    )
    print("3. Refuse an existing test plist/service. Install only the following test descriptor:")
    with (
        patch.object(automation, "LABEL", TEST_LABEL),
        patch.dict(
            os.environ,
            {"TRAVEL_MODE_CONFIG": str(root / "config.toml"), "TRAVEL_MODE_STATE": str(root / "state.json")},
        ),
    ):
        print(plistlib.dumps(automation.agent_plist(str(helper))).decode())
    for step, command in [
        ("4. Bootstrap", ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]),
        ("5. Check idle state", ["/bin/launchctl", "print", f"gui/{os.getuid()}/{TEST_LABEL}"]),
    ]:
        print(step + ": " + shlex.join(command))
    print("6. Manually unplug or reconnect power. Wait for the finite handler to finish, then press Enter.")
    print("   Query /usr/bin/pmset -g ps before and after; change source once and keep the new source.")
    print("7. Read " + str(root / "automation.json") + "; require a handled receipt with actual source.")
    print(
        "8. Confirm no running process: "
        + shlex.join(["/bin/launchctl", "print", f"gui/{os.getuid()}/{TEST_LABEL}"])
    )
    print(
        "9. Finally unload exactly this test label: "
        + shlex.join(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{TEST_LABEL}"])
    )
    print("10. Delete only " + str(path) + "; retain isolated evidence. Never send notifyutil -p.")


def child(root):
    if sys.argv[1:] != ["event", "power-changed", "--launch-event"]:
        raise GuardViolation("Unexpected test helper arguments")
    guard = EventGuard(
        shell.subprocess_runner,
        Path.home() / "Library/LaunchAgents" / (TEST_LABEL + ".plist"),
        root / "event-helper",
        child=True,
    )
    shell.set_runner(guard)
    return cli.main(sys.argv[1:])


def run(output=None):
    from harness_temporary import fixture_directory

    from travel_mode.system.platform import ensure_macos

    ensure_macos()
    output = output or Path("/tmp/tui-event-e2e") / str(time.time_ns())
    output.mkdir(parents=True, exist_ok=False)
    with fixture_directory(output, "travel-mode-event-e2e-") as root:
        return run_fixture(root, output)


def run_fixture(root, output):
    (root / "config.toml").write_text('[automation]\non_battery="off"\non_ac="off"\n')
    helper = root / "event-helper"
    helper.write_text(
        f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
        "from e2e_automation import child\n"
        f"raise SystemExit(child(Path({str(root)!r})))\n"
    )
    helper.chmod(0o700)
    path = Path.home() / "Library/LaunchAgents" / (TEST_LABEL + ".plist")
    if path.exists():
        raise GuardViolation("Existing test plist: " + str(path))
    guard = EventGuard(shell.subprocess_runner, path, helper)
    original = shell.get_runner()
    with (
        patch.object(automation, "LABEL", TEST_LABEL),
        patch.object(automation, "executable", lambda: str(helper)),
        patch.dict(
            os.environ,
            {"TRAVEL_MODE_CONFIG": str(root / "config.toml"), "TRAVEL_MODE_STATE": str(root / "state.json")},
        ),
    ):
        shell.set_runner(guard)
        try:
            Executor(Context(None)).execute(InstallAutomation())
            print("Installed test agent; evidence: " + str(root))
            print(automation.status())
            from travel_mode.flows.automation import power_source

            previous_source, started = power_source(), time.time()
            input("Change the power source manually. After the handler exits, press Enter: ")
            result = automation.status()
            current_source = power_source()
            if (
                not result["loaded"]
                or result["running"]
                or not result["last_event"]
                or result["last_event"]["result"] != "handled"
                or result["last_event"]["source"] != current_source
                or result["last_event"]["at"] < started
                or current_source == previous_source
            ):
                raise AssertionError("Event delivery/idle check failed: " + repr(result))
            print("PASS: real event consumed; handler exited; no notification or power mutation")
        finally:
            try:
                if path.exists():
                    Executor(Context(None)).execute(UninstallAutomation())
            finally:
                shell.set_runner(original)
                write_json(root / "guard.json", guard.calls)
    print("Test agent removed; retained evidence: " + str(output))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.plan:
        plan()
        return 0
    return run(args.output)


if __name__ == "__main__":
    raise SystemExit(main())

"""Fail-closed command boundary for the manually invoked local e2e harness."""

from __future__ import annotations

import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

from travel_mode.system.health import PROCESS_COMMAND, native_command
from travel_mode.system.shell import CmdResult, command_path, subprocess_runner

PROCESS_QUERY = ["ps", "-axo", "pid=,ppid=,stat=,lstart=,comm="]
STATUS_QUERY = ["ps", "-axo", "pid=,stat=,comm="]
POWER_KEYS = frozenset(
    {
        "displaysleep",
        "sleep",
        "powernap",
        "lowpowermode",
        "powermode",
        "hibernatemode",
        "tcpkeepalive",
        "womp",
        "standby",
        "ttyskeepawake",
        "disksleep",
        "lessbright",
        "networkoversleep",
    }
)
GRACEFUL_ID = "com.example.travelmode-e2e.graceful"
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
)
QUIT_EVENT = [
    "osascript",
    "-e",
    "with timeout of 8 seconds",
    "-e",
    f'tell application id "{GRACEFUL_ID}" to quit',
    "-e",
    "end timeout",
]
LABEL = "com.example.travelmode-e2e.agent"


class GuardViolation(PermissionError):
    """An unapproved command aborts the harness before it reaches a backend."""


class LocalBackend:
    """Only GuardRunner may call this backend, including process creation."""

    def __init__(self, executables):
        self.executables = set(executables)
        self.children = []

    def __call__(self, cmd, timeout=None, interactive=False):
        if len(cmd) == 1 and cmd[0] in self.executables:
            child = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self.children.append(child)
            return CmdResult(str(child.pid), "", 0)
        return subprocess_runner(cmd, timeout, interactive)

    def reap(self):
        for child in self.children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Fixture child {child.pid} did not exit during cleanup") from exc


def identities(output):
    rows = {}
    for line in output.splitlines():
        parts = line.split(None, 8)
        if len(parts) != 9 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(f"Invalid process identity row: {line!r}")
        rows[int(parts[0])] = (parts[2], " ".join(parts[3:8]), parts[8])
    return rows


class GuardRunner:
    """Exact argv allowlist; signal permissions include process birth identity."""

    def __init__(self, backend, root: Path, apps, plist: Path, *, plan=False):
        self.backend = backend
        self.root = root.resolve()
        self.apps = tuple(Path(app).resolve() for app in apps)
        self.plist = plist.resolve()
        if not self.plist.is_relative_to(self.root) or any(
            not app.is_relative_to(self.root) for app in self.apps
        ):
            raise GuardViolation("All fixtures must be inside the temporary root")
        self.executables = tuple(str(app / "Contents/MacOS/loop") for app in self.apps)
        self.shells = tuple(
            str(app / f"Contents/{directory}/sh") for app in self.apps for directory in ("MacOS", "Helpers")
        )
        self.graceful = self.root / "TravelModeE2E-Graceful.app"
        self.shells += (str(self.graceful / "Contents/MacOS/applet"),)
        self.owned = {}
        self.plan = plan
        self.commands = []
        self.tripped = False
        self.results = []
        self.trace = []
        self.violations = []

    def refuse(self, cmd):
        self.tripped = True
        self.violations.append(list(cmd))
        raise GuardViolation(f"E2E GUARD REFUSED: {shlex.join(cmd)}")

    def observe(self):
        result = self(PROCESS_QUERY)
        if result.rc:
            raise RuntimeError(f"Cannot inspect fixture identities: {result.stderr}")
        rows = identities(result.stdout)
        for pid, (_, started, command) in rows.items():
            if command in self.shells:
                self.owned[pid] = (started, command)
        return rows

    def validate(self, command):
        cmd = list(command)
        if cmd[:3] == ["sudo", "-n", "-l"]:
            target = self.validate(["sudo", *cmd[3:]])
            if target[:3] != ["sudo", "-n", "/usr/bin/pmset"]:
                self.refuse(cmd)
            return ["sudo", "-n", "-l", *target[2:]]
        # Documented pmset-only sudo policy. Product journal sees the original argv.
        if cmd[:2] in (["sudo", "pmset"], ["sudo", "/usr/bin/pmset"]):
            cmd = ["sudo", "-n", "/usr/bin/pmset", *cmd[2:]]
        if cmd[:1] == ["pmset"]:
            cmd = ["/usr/bin/pmset", *cmd[1:]]
        allowed = cmd in [
            PROCESS_COMMAND,
            native_command("displays"),
            native_command("brightness"),
            ["ioreg", "-rn", "AppleSmartBattery"],
            ["lsappinfo", "front"],
            PROCESS_QUERY,
            STATUS_QUERY,
            ["ps", "-axo", "pid,stat,comm"],
            ["pgrep", "-fl", "caffeinate"],
            ["mdutil", "-s", "/"],
            ["networksetup", "-listallhardwareports"],
            ["networksetup", "-getairportpower", "en0"],
            ["tmutil", "status"],
            ["top", "-l", "1", "-n", "0"],
            ["top", "-l", "2", "-stats", "pid,power,command", "-o", "power", "-n", "5"],
            ["ps", "-axo", "pid=,%cpu=,command="],
            ["ps", "-ww", "-axo", "pid=,command="],
            ["launchctl", "list"],
            ["mdfind", 'kMDItemContentType == "com.apple.application-bundle"'],
        ]
        allowed |= cmd in [
            ["/usr/bin/pmset", "-g", kind] for kind in ("custom", "cap", "ps", "assertions", "batt", "log")
        ]
        allowed |= (
            len(cmd) == 5
            and cmd[:4] == ["lsappinfo", "info", "-only", "pid,bundleid"]
            and bool(re.fullmatch(r"ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+:", cmd[4]))
        )
        allowed |= (
            len(cmd) == 6
            and cmd[:3] == ["ps", "-ww", "-p"]
            and cmd[3].isdigit()
            and cmd[4:] == ["-o", "command="]
        )
        allowed |= (
            len(cmd) == 5 and cmd[:2] == ["ps", "-p"] and cmd[2].isdigit() and cmd[3:] == ["-o", "%cpu="]
        )
        allowed |= cmd in [["/usr/bin/pmset", "-g"], ["sudo", "-n", "/usr/bin/pmset", "-g", "ps"]]
        allowed |= cmd in [
            [
                "osacompile",
                "-s",
                "-o",
                str(self.graceful),
                "-e",
                "on idle",
                "-e",
                "return 1",
                "-e",
                "end idle",
            ],
            ["codesign", "--force", "--sign", "-", str(self.graceful)],
            ["open", "-g", "-j", str(self.graceful)],
            [LSREGISTER, "-u", str(self.graceful)],
            QUIT_EVENT,
        ]
        if cmd == ["open", str(self.graceful)]:
            return ["open", "-g", "-j", str(self.graceful)]
        if cmd[:4] == ["sudo", "-n", "/usr/bin/pmset", "-a"]:
            allowed |= cmd[4:] in [["disablesleep", "0"], ["disablesleep", "1"]]
        if cmd[:4] == ["sudo", "-n", "/usr/bin/pmset", "-c"]:
            allowed |= (
                len(cmd) == 8
                and cmd[4] == "powernap"
                and cmd[6] == "womp"
                and cmd[5] in {"0", "1"}
                and cmd[7] in {"0", "1"}
            )
        alias = self.root / "home/Library/LaunchAgents" / f"{LABEL}.plist"
        if (
            len(cmd) == 3
            and cmd[:2] in (["launchctl", "load"], ["launchctl", "unload"])
            and cmd[2] == str(alias)
            and alias.resolve() == self.plist
        ):
            cmd[2] = str(self.plist)
        allowed |= cmd in [["launchctl", action, str(self.plist)] for action in ("load", "unload")]
        allowed |= cmd in [[exe] for exe in self.executables]
        # Reopen only these bundles, by directly executing their fixture entrypoint.
        for app, exe in zip(self.apps, self.executables, strict=True):
            if cmd == ["open", str(app)]:
                return [exe]
        if cmd[:4] == ["sudo", "-n", "/usr/bin/pmset", "-b"]:
            values = cmd[4:]
            allowed |= (
                bool(values)
                and len(values) % 2 == 0
                and len(set(values[::2])) == len(values[::2])
                and all(key in POWER_KEYS for key in values[::2])
                and all(value.isdecimal() and 0 <= int(value) <= 180 for value in values[1::2])
                and all(
                    (key != "hibernatemode" or value in {"0", "3", "25"})
                    and (key not in {"powernap", "lowpowermode", "powermode"} or int(value) <= 2)
                    for key, value in zip(values[::2], values[1::2], strict=True)
                )
            )
        if (
            len(cmd) == 3
            and cmd[0] == "kill"
            and cmd[1] in {"-STOP", "-CONT", "-TERM", "-KILL"}
            and cmd[2].isdigit()
            and int(cmd[2]) in self.owned
        ):
            return cmd
        if not allowed:
            self.refuse(cmd)
        return cmd

    def __call__(self, command, timeout=None, interactive=False):
        cmd = self.validate(command)
        if interactive:
            self.refuse(cmd)
        self.commands.append(tuple(cmd))
        if self.plan:
            print(shlex.join(cmd))
            return CmdResult("", "", 0)
        if cmd[:1] == ["kill"]:
            result = self(PROCESS_QUERY, timeout=10)
            if result.rc:
                raise RuntimeError(f"Cannot validate signal identity: {result.stderr}")
            row = identities(result.stdout).get(int(cmd[2]))
            expected = self.owned[int(cmd[2])]
            if row is None or (row[1], row[2]) != expected:
                self.refuse(cmd)
        event = dict(
            command=list(cmd),
            timeout=timeout,
            thread=threading.current_thread().name,
            executable=command_path(cmd[0]),
        )
        self.trace.append(event)
        started = time.monotonic()
        try:
            result = self.backend(cmd, timeout, interactive)
        finally:
            event["seconds"] = time.monotonic() - started
        event.update(rc=result.rc, stdout_bytes=len(result.stdout.encode()), stderr=result.stderr)
        self.results.append((tuple(cmd), result))
        return result

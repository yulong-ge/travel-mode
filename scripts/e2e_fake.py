"""Stateful command double shared by tmux demo and harness tests. Never invokes subprocesses."""

import os
import plistlib
from pathlib import Path

import e2e_graceful
import e2e_refusal
from e2e_guard import GRACEFUL_FIXTURES, LABEL, PROCESS_QUERY, QUIT_EVENTS, STATUS_QUERY

from travel_mode.system.health import PROCESS_COMMAND, native_command
from travel_mode.system.shell import CmdResult


class Machine:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.rows = {77: ("S", "Thu Oct 15 12:00:00 2026", "/Applications/Real.app/Contents/MacOS/Real")}
        self.loaded = False
        self.next_pid = 900001
        self.battery = {
            "displaysleep": "10",
            "sleep": "15",
            "powernap": "1",
            "SleepServices": "1",
            "lowpowermode": "0",
            "hibernatemode": "3",
            "womp": "1",
        }
        self.ac = {
            "sleep": "25",
            "displaysleep": "10",
            "powernap": "1",
            "SleepServices": "1",
            "womp": "1",
            "lowpowermode": "0",
        }
        self.sleep_disabled = "1"
        self.permission = True
        self.fail_restore = False
        self.tcc = None

    def power_text(self):
        return (
            "Battery Power:\n"
            + "".join(f" {k} {v}\n" for k, v in self.battery.items())
            + "AC Power:\n"
            + "".join(f" {k} {v}\n" for k, v in self.ac.items())
        )

    def __call__(self, cmd, timeout=None, interactive=False):
        if interactive:
            raise RuntimeError("Fake machine never accepts interactive authentication")
        self.calls.append(tuple(cmd))
        from travel_mode.system.shell import command_started

        command_started()
        if cmd == ["defaults", "read", "com.ameba.SwiftBar", "PluginDirectory"]:
            return CmdResult("", "fixture: SwiftBar is not installed", 1)
        if cmd == native_command("foreground"):
            return CmdResult(
                '{"status":"unavailable","source":"NSWorkspace","reason":"fixture has no GUI session"}', "", 0
            )
        if cmd == native_command("displays"):
            return CmdResult("[]", "", 0)
        if cmd == native_command("brightness"):
            return CmdResult("0.5", "", 0)
        if cmd == ["tmutil", "status"]:
            return CmdResult("Backup session status:\n{ Running = 0; }", "", 0)
        if cmd == ["lsappinfo", "front"]:
            return CmdResult("ASN:0x0-0x1:", "", 0)
        if cmd[:2] == ["lsappinfo", "info"]:
            return CmdResult('pid = 77\nbundleID="org.demo.foreground"', "", 0)
        if cmd == PROCESS_COMMAND:
            return CmdResult(
                "\n".join(f"{p} 1 {os.getuid()} {s} 0.0 {b} {c}" for p, (s, b, c) in self.rows.items()), "", 0
            )
        if cmd == ["ioreg", "-a", "-rn", "AppleSmartBattery"]:
            return CmdResult(
                plistlib.dumps(
                    [
                        {
                            "AppleRawCurrentCapacity": 4000,
                            "ExternalConnected": True,
                            "IsCharging": False,
                            "Voltage": 12000,
                        }
                    ]
                ).decode("utf-8"),
                "",
                0,
            )
        if cmd[:3] == ["sudo", "-n", "-l"]:
            return CmdResult(
                "NOPASSWD: ALL" if self.permission else "",
                "" if self.permission else "sudo denied",
                int(not self.permission),
            )
        if cmd == PROCESS_QUERY:
            out = "\n".join(f"{p} 1 {s} {b} {c}" for p, (s, b, c) in self.rows.items())
        elif cmd == STATUS_QUERY or cmd == ["ps", "-axo", "pid,stat,comm"]:
            out = "\n".join(f"{p} {s} {c}" for p, (s, _, c) in self.rows.items())
        elif cmd == ["ps", "-axo", "pid=,%cpu=,command="]:
            out = "\n".join(f"{p} 0.0 {c}" for p, (_, _, c) in self.rows.items())
        elif cmd[:1] == ["swiftc"]:
            Path(cmd[-1]).write_text("fixture executable; never run on the host\n")
            out = ""
        elif cmd == e2e_refusal.QUIT_EVENT:
            return CmdResult("", "execution error: User canceled. (-128)", 1)
        elif cmd[:1] == ["osacompile"]:
            app = Path(cmd[3])
            (app / "Contents/MacOS").mkdir(parents=True)
            (app / "Contents/Info.plist").write_bytes(
                plistlib.dumps({"CFBundlePackageType": "APPL", "CFBundleExecutable": "applet"})
            )
            return CmdResult("", "", 0)
        elif cmd[:1] == ["codesign"] or (cmd and cmd[0].endswith("/lsregister")):
            out = ""
        elif cmd[:1] == ["open"] or (len(cmd) == 1 and cmd[0].endswith("/Contents/MacOS/loop")):
            app = Path(cmd[-1]) if cmd[0] == "open" else Path(cmd[0]).parents[2]
            executables = ["MacOS/refusing"] if app.stem == "TravelModeE2E-Refusing" else ["MacOS/applet"]
            if cmd[0] == "open" and any(
                r[2] == str(app / "Contents" / executables[0]) for r in self.rows.values()
            ):
                return CmdResult("", "", 0)
            for executable in executables if cmd[0] == "open" else ["MacOS/sh", "Helpers/sh"]:
                self.rows[self.next_pid] = (
                    "S",
                    "Thu Oct 15 12:00:00 2026",
                    str(app / "Contents" / executable),
                )
                self.next_pid += 1
            if cmd[0] == "open" and app in [
                self.root / f"TravelModeE2E-{n}.app" for n, _ in GRACEFUL_FIXTURES
            ]:
                e2e_graceful.receipts(app)[0].write_text("ready")
            out = ""
        elif cmd in QUIT_EVENTS or cmd in [
            e2e_graceful.quit_command(self.root / f"TravelModeE2E-{n}.app") for n, _ in GRACEFUL_FIXTURES
        ]:
            if self.tcc:
                return CmdResult("", self.tcc, 124 if self.tcc == "timeout" else 1)
            event_index = (
                QUIT_EVENTS.index(cmd)
                if cmd in QUIT_EVENTS
                else next(
                    i
                    for i, (name, _) in enumerate(GRACEFUL_FIXTURES)
                    if cmd == e2e_graceful.quit_command(self.root / f"TravelModeE2E-{name}.app")
                )
            )
            name = GRACEFUL_FIXTURES[event_index][0]
            e2e_graceful.receipts(self.root / f"TravelModeE2E-{name}.app")[1].write_text("quit")
            path = str(self.root / f"TravelModeE2E-{name}.app")
            self.rows = {p: r for p, r in self.rows.items() if not r[2].startswith(path + "/")}
            out = ""
        elif cmd[:1] == ["kill"]:
            pid = int(cmd[2])
            old = self.rows[pid]
            if cmd[1] in {"-TERM", "-KILL"}:
                del self.rows[pid]
            else:
                self.rows[pid] = ("T" if cmd[1] == "-STOP" else "S", *old[1:])
            out = ""
        elif cmd[:1] == ["launchctl"]:
            if cmd[1] == "list":
                out = f"- 0 {LABEL}\n" if self.loaded else "PID Status Label\n"
            else:
                self.loaded = cmd[1] == "load"
                out = ""
        elif cmd == ["sysctl", "-n", "kern.sleeptime", "kern.waketime"]:
            return CmdResult("{ sec = 0, usec = 0 }\n{ sec = 0, usec = 0 }\n", "", 0)
        elif cmd == ["/usr/bin/pmset", "-g", "log"]:
            out = ""
        elif cmd == ["/usr/bin/pmset", "-g", "custom"]:
            out = self.power_text()
        elif cmd == ["/usr/bin/pmset", "-g", "cap"]:
            out = " ".join(self.battery)
        elif cmd == ["/usr/bin/pmset", "-g"]:
            out = f"SleepDisabled {self.sleep_disabled}\n"
        elif cmd in [["/usr/bin/pmset", "-g", kind] for kind in ("ps", "batt")]:
            out = "Now drawing from 'AC Power'\n -InternalBattery-0 100%; charged;\n"
        elif cmd == ["/usr/bin/pmset", "-g", "assertions"]:
            out = "Assertion status system-wide:\n"
        elif cmd[:3] == ["sudo", "-n", "/usr/bin/pmset"]:
            values = dict(zip(cmd[4::2], cmd[5::2], strict=True))
            if self.fail_restore and values.get("sleep") == "15":
                return CmdResult("", "restore denied", 1)
            if cmd[3] == "-b":
                self.battery.update(values)
                if "powernap" in values:
                    self.battery["SleepServices"] = values["powernap"]
            elif cmd[3] == "-c":
                self.ac.update(values)
                if "powernap" in values:
                    self.ac["SleepServices"] = values["powernap"]
            elif cmd[3] == "-a":
                self.sleep_disabled = values["disablesleep"]
            else:
                raise RuntimeError(f"Unexpected fake power command: {cmd}")
            out = ""
        elif cmd in [
            ["top", "-l", "1", "-n", "0"],
            ["top", "-l", "2", "-stats", "pid,power,command", "-o", "power", "-n", "5"],
        ]:
            out = ""
        elif cmd[:1] == ["ps"] and cmd[1:2] == ["-p"]:
            out = "0.0"
        elif cmd[:1] == ["mdfind"]:
            out = ""
        elif cmd == ["mdutil", "-s", "/"]:
            out = "/: Indexing enabled."
        elif cmd == ["pgrep", "-fl", "caffeinate"]:
            return CmdResult("", "", 1)
        elif cmd == ["networksetup", "-listallhardwareports"]:
            out = "Hardware Port: Wi-Fi\nDevice: en0\n"
        elif cmd == ["networksetup", "-getairportpower", "en0"]:
            out = "Wi-Fi Power (en0): On"
        else:
            raise RuntimeError(f"Unexpected fake command: {cmd}")
        return CmdResult(out, "", 0)


class VirtualMachine(Machine):
    """Recorded hosted VM capabilities, with no Battery Power profile or sleep switch."""

    recordings = Path(__file__).resolve().parents[1] / "tests/fixtures/hosted-vm"

    def __init__(self, root):
        super().__init__(root)
        from travel_mode.system.pmset import parse_custom

        self.battery = {}
        self.ac = parse_custom(self.power_text())["a"]
        self.tcc = "Not authorized to send Apple events (-1743)"

    def power_text(self):
        return (self.recordings / "pmset-custom-recorded.txt").read_text()

    def __call__(self, cmd, timeout=None, interactive=False):
        if cmd in (["/usr/bin/pmset", "-g"], ["ioreg", "-a", "-rn", "AppleSmartBattery"]):
            self.calls.append(tuple(cmd))
            filename = "pmset-g-synthetic.txt" if cmd[0] == "/usr/bin/pmset" else "ioreg-empty-recorded.txt"
            return CmdResult(
                (self.recordings / filename).read_text()
                if cmd[0] == "/usr/bin/pmset"
                else plistlib.dumps([]).decode(),
                "",
                0,
            )
        return super().__call__(cmd, timeout, interactive)


class InstantClock:
    """Finite-window demo clock; advances without sleeping or a sampler thread."""

    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class BatteryMachine(Machine):
    """Numerical battery observations respond to only the owned Burn fixture."""

    def __init__(self, root):
        super().__init__(root)
        self.external = False
        self.battery_reads = 0
        self.telemetry_count = 100
        self.telemetry_sum = 1_000_000
        self.cached_battery = None

    def __call__(self, cmd, timeout=None, interactive=False):
        if cmd == ["ioreg", "-a", "-rn", "AppleSmartBattery"]:
            self.calls.append(tuple(cmd))
            self.battery_reads += 1
            frozen = any("/TravelModeE2E-Burn.app/" in r[2] and r[0] == "T" for r in self.rows.values())
            current = -700 if frozen else -2000
            if self.cached_battery is None or self.battery_reads % 4 == 0:
                self.telemetry_count += 20
                self.telemetry_sum += (-current * 12) * 20
                self.cached_battery = dict(
                    ChargerData={"IsCharging": 0},
                    IsCharging=False,
                    Voltage=12000,
                    InstantAmperage=2**64 + current,
                    TimeRemaining=200,
                    UpdateTime=1000 + self.telemetry_count,
                    BatteryData=dict(RemainingCapacity=4800 - self.battery_reads, FullChargeCapacity=5000),
                    PowerTelemetryData=dict(
                        AccumulatedSystemLoad=self.telemetry_sum,
                        SystemLoadAccumulatorCount=self.telemetry_count,
                        SystemLoad=-current * 12,
                        AccumulatedBatteryDischarge=2**64 - self.telemetry_sum,
                        BatteryDischargeAccumulatorCount=self.telemetry_count,
                    ),
                )
            return CmdResult(
                plistlib.dumps([dict(self.cached_battery, ExternalConnected=self.external)]).decode(), "", 0
            )
        if cmd in [["/usr/bin/pmset", "-g", kind] for kind in ("ps", "batt", "log")]:
            self.calls.append(tuple(cmd))
            if cmd[-1] == "log":
                return CmdResult("2020-01-01 00:00:00 Using Batt\n", "", 0)
            source = "AC Power" if self.external else "Battery Power"
            return CmdResult(
                f"Now drawing from '{source}'\n -InternalBattery-0 96%; discharging; 3:20 remaining\n", "", 0
            )
        return super().__call__(cmd, timeout, interactive)


class SimulatedBrightness:
    """Native API-shaped demo double. Never loads system frameworks."""

    def __init__(self):
        self.value = 0.49684539437294006
        self.calls = []

    def get(self):
        return self.value

    def set(self, value):
        self.calls.append(value)
        self.value = value
        return "", 0

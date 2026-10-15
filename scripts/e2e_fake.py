"""Stateful command double shared by tmux demo and harness tests. Never invokes subprocesses."""

import plistlib
from pathlib import Path

from e2e_guard import LABEL, PROCESS_QUERY, QUIT_EVENT, STATUS_QUERY

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
        if cmd[:3] == ["sudo", "-n", "-l"]:
            return CmdResult("", "" if self.permission else "sudo denied", int(not self.permission))
        if cmd == PROCESS_QUERY:
            out = "\n".join(f"{p} 1 {s} {b} {c}" for p, (s, b, c) in self.rows.items())
        elif cmd == STATUS_QUERY or cmd == ["ps", "-axo", "pid,stat,comm"]:
            out = "\n".join(f"{p} {s} {c}" for p, (s, _, c) in self.rows.items())
        elif cmd == ["ps", "-axo", "pid=,%cpu=,command="]:
            out = "\n".join(f"{p} 0.0 {c}" for p, (_, _, c) in self.rows.items())
        elif cmd[:1] == ["osacompile"]:
            app = Path(cmd[3])
            (app / "Contents/MacOS").mkdir(parents=True)
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "applet"}))
            return CmdResult("", "", 0)
        elif cmd[:1] == ["codesign"] or (cmd and cmd[0].endswith("/lsregister")):
            out = ""
        elif cmd[:1] == ["open"] or (len(cmd) == 1 and cmd[0].endswith("/Contents/MacOS/loop")):
            app = Path(cmd[-1]) if cmd[0] == "open" else Path(cmd[0]).parents[2]
            for executable in ["MacOS/applet"] if cmd[0] == "open" else ["MacOS/sh", "Helpers/sh"]:
                self.rows[self.next_pid] = (
                    "S",
                    "Thu Oct 15 12:00:00 2026",
                    str(app / "Contents" / executable),
                )
                self.next_pid += 1
            out = ""
        elif cmd == QUIT_EVENT:
            if self.tcc:
                return CmdResult("", self.tcc, 124 if self.tcc == "timeout" else 1)
            self.rows = {p: r for p, r in self.rows.items() if not r[2].endswith("/Contents/MacOS/applet")}
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

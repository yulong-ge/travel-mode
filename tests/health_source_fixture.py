"""Generic process observations shared by provenance and interface regressions."""

import os
import plistlib
from pathlib import Path

from travel_mode.system.health import PROCESS_COMMAND
from travel_mode.system.shell import CmdResult


def populate(runner, *, child=900100, parent=True, load=False, awake_app=False):
    def process(pid, ppid, executable, cpu=0):
        return f"{pid} {ppid} {os.getuid()} S {cpu} Thu Oct 15 12:00:00 2026 {executable}"

    rows = [
        process(900000, 1, "/System/Library/CoreServices/loginwindow"),
        process(child, 900102, "/usr/bin/caffeinate"),
    ]
    if parent:
        rows += [process(900102, 900101, "/bin/sh"), process(900101, 1, "/opt/demo/bin/claude")]
    if awake_app:
        bundle = Path.home() / "Applications/Amphetamine.app"
        (bundle / "Contents").mkdir(parents=True, exist_ok=True)
        (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundlePackageType": "APPL"}))
        rows += [process(900300, 1, f"{bundle}/Contents/MacOS/Amphetamine")]
    if load:
        rows += [
            process(900200 + i, 1, f"/opt/demo/bin/worker-{i}", cpu) for i, cpu in enumerate((85, 65, 55, 45))
        ]
    runner.responses[tuple(PROCESS_COMMAND)] = CmdResult("\n".join(rows), "", 0)
    runner.responses[("pmset", "-g")] = CmdResult("SleepDisabled 0\nlowpowermode 1", "", 0)
    runner.responses[("ps", "-ww", "-p", str(child), "-o", "command=")] = CmdResult(
        "/usr/bin/caffeinate -i -t 300", "", 0
    )
    runner.responses[("pmset", "-g", "assertions")] = CmdResult(
        "Assertion status system-wide:\n"
        f'pid {child}(caffeinate): 00:03:00 PreventUserIdleSystemSleep named: "task"',
        "",
        0,
    )

"""Measure one simulated event; never install an agent or invoke a host command."""

import argparse
import json
import os
import resource
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from travel_mode.flows.automation import event
from travel_mode.system import shell


def measure():
    calls = []

    def simulated(command, timeout=None, interactive=False):
        if interactive:
            raise RuntimeError("Simulation forbids authentication")
        calls.append(command)
        if command == ["pmset", "-g", "ps"]:
            return shell.CmdResult("Battery Power", "", 0)
        if command[:1] == ["osascript"]:
            return shell.CmdResult("", "", 0)
        raise RuntimeError("Unexpected simulated command: " + repr(command))

    original = shell.get_runner()
    with tempfile.TemporaryDirectory(prefix="travel-mode-event-overhead-") as directory:
        root = Path(directory)
        with patch.dict(
            os.environ,
            {"TRAVEL_MODE_CONFIG": str(root / "config.toml"), "TRAVEL_MODE_STATE": str(root / "state.json")},
        ):
            shell.set_runner(simulated)
            try:
                cpu = resource.getrusage(resource.RUSAGE_SELF)
                children = resource.getrusage(resource.RUSAGE_CHILDREN)
                started = time.perf_counter()
                result = event()
                elapsed = time.perf_counter() - started
                ended = resource.getrusage(resource.RUSAGE_SELF)
                child_end = resource.getrusage(resource.RUSAGE_CHILDREN)
            finally:
                shell.set_runner(original)
    return {
        "method": (
            "One simulated battery notification, imports already loaded; temporary paths; "
            "no host commands or XPC registration"
        ),
        "exit_code": result,
        "wall_seconds": elapsed,
        "self_cpu_seconds": ended.ru_utime + ended.ru_stime - cpu.ru_utime - cpu.ru_stime,
        "children_cpu_seconds": child_end.ru_utime
        + child_end.ru_stime
        - children.ru_utime
        - children.ru_stime,
        "logical_calls": calls,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(measure(), indent=2) + "\n"
    if args.output:
        args.output.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()

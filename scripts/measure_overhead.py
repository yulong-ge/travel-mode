"""Read-only CPU accounting for finite battery probes or an open, idle TUI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import tempfile
import time
from pathlib import Path


def cpu_times():
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "self_seconds": own.ru_utime + own.ru_stime,
        "children_seconds": children.ru_utime + children.ru_stime,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("probes", "tui"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix="travel-mode-overhead-") as temporary:
        root = Path(temporary)
        for name, path in {
            "HOME": root / "home",
            "XDG_CONFIG_HOME": root / "config",
            "XDG_STATE_HOME": root / "state",
            "TRAVEL_MODE_CONFIG": root / "config.toml",
            "TRAVEL_MODE_STATE": root / "state.json",
        }.items():
            os.environ[name] = str(path)
        (root / "home").mkdir()
        (root / "config.toml").write_text('[options]\nscenario="work"\nlanguage="en"\n')
        from travel_mode.system import battery
        from travel_mode.system.shell import ReadOnlyRunner, set_runner

        class CountingRunner(ReadOnlyRunner):
            def __init__(self):
                self.calls = 0
                self.all_commands = []

            def __call__(self, command, timeout=None, interactive=False):
                self.all_commands.append(list(command))
                self.calls += int(command == battery.COMMAND)
                return super().__call__(command, timeout, interactive)

        runner = CountingRunner()
        set_runner(runner)

        def result(before, started, start_calls, **extra):
            after = cpu_times()
            delta = {key: after[key] - before[key] for key in after}
            return {
                "mode": args.mode,
                "wall_seconds": time.monotonic() - started,
                **delta,
                "total_cpu_seconds": sum(delta.values()),
                "ioreg_calls": runner.calls - start_calls,
                **extra,
            }

        if args.mode == "probes":
            from travel_mode.measurement import macos_estimate

            before, started = cpu_times(), time.monotonic()
            readings = []
            for index in range(10):
                delay = started + 2 * index - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                readings.append(battery.read())
            delay = started + 20 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            data = result(before, started, 0, observations=readings, macos=macos_estimate())
        else:
            from textual.worker import WorkerCancelled, WorkerFailed, WorkerState

            from travel_mode.tui.app import TravelApp

            class MeasuredApp(TravelApp):
                def on_worker_state_changed(self, event):
                    if event.worker.group == "dash" and event.state == WorkerState.SUCCESS:
                        self.benchmark_task = asyncio.create_task(self.benchmark())

                async def benchmark(self):
                    try:
                        if not self.refresh_complete:
                            raise RuntimeError("TUI startup did not complete")
                        await asyncio.sleep(1)
                        before, started, start_calls = cpu_times(), time.monotonic(), runner.calls
                        command_start = len(runner.all_commands)
                        await asyncio.sleep(300)
                        self.exit(
                            result(
                                before,
                                started,
                                start_calls,
                                idle_commands=runner.all_commands[command_start:],
                            )
                        )
                    except (RuntimeError, OSError, ValueError, WorkerCancelled, WorkerFailed) as exc:
                        self.exit(exc)

            data = MeasuredApp(scenario="work").run()
            if isinstance(data, Exception):
                raise data
            if not isinstance(data, dict):
                raise RuntimeError("TUI exited before CPU accounting completed")
        output.write_text(json.dumps(data, indent=2) + "\n")
        print(json.dumps(data))


if __name__ == "__main__":
    main()

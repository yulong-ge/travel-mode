"""Run finite concurrent hermetic pytest suites; never launch the real product."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "tests/tui",
    "tests/characterization/test_tui_contract.py",
    "tests/characterization/test_resurrection_flow.py",
    "tests/characterization/test_dry_run_messages.py",
    "tests/characterization/test_work_flow.py",
    "tests/e2e_harness/test_isolation.py",
    "tests/unit/test_review_ui.py",
    "tests/unit/test_review_deferred.py",
    "tests/unit/test_tui_extra_app_dirs.py",
    "tests/unit/test_caffeinate_hibernation.py",
    "tests/unit/test_tui_debug.py",
    "tests/unit/test_scenarios.py",
    "tests/unit/test_config_write_boundaries.py",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("targets", nargs="*")
    args = parser.parse_args()
    if args.jobs < 1 or args.runs < 1:
        parser.error("jobs and runs must be positive")
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv must be available in PATH")
    output = args.output or Path(tempfile.mkdtemp(prefix="travel-mode-stress-"))
    if args.output:
        output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    targets = args.targets or TARGETS

    def run(index):
        folder = output / f"run-{index:03d}"
        folder.mkdir()
        for name in ("home", "tmp"):
            (folder / name).mkdir()
        env = {
            **os.environ,
            "HOME": str(folder / "home"),
            "TMPDIR": str(folder / "tmp"),
            "COVERAGE_FILE": str(folder / ".coverage"),
        }
        command = [uv, "run", "--no-sync", "pytest", "-q", f"--junitxml={folder / 'results.xml'}"]
        if args.coverage:
            command += ["--cov=travel_mode", "--cov-report=", "--cov-fail-under=0"]
        started = time.monotonic()
        with (folder / "pytest.log").open("w") as log:
            result = subprocess.run(
                [*command, *targets], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        row = dict(run=index, rc=result.returncode, seconds=time.monotonic() - started)
        print(json.dumps(row), flush=True)
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, range(1, args.runs + 1)))
    summary = dict(jobs=args.jobs, runs=args.runs, targets=targets, results=results)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    failed = sum(row["rc"] != 0 for row in results)
    print(f"{args.runs - failed}/{args.runs} suites passed; {output}")
    return int(failed != 0)


if __name__ == "__main__":
    raise SystemExit(main())

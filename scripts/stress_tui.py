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
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Include every file with an asynchronous test, including characterization and
# harness paths outside tests/tui. These are finite hermetic pytest children.
TARGETS = ["tests/tui"] + [
    str(path.relative_to(ROOT))
    for path in sorted((ROOT / "tests").rglob("test_*.py"))
    if path.parent != ROOT / "tests/tui"
    and ("async def test_" in path.read_text() or ".run_test(" in path.read_text())
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("targets", nargs="*")
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.runs < 1:
        parser.error("jobs and runs must be positive")
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv must be available in PATH")
    with ExitStack() as cleanup:
        if args.output:
            output = args.output
            output.mkdir(parents=True, exist_ok=False)
        else:
            output = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix="travel-mode-stress-")))
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

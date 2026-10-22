"""Read-only clean-runner acceptance with isolated travel-mode paths and provenance checks."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import runpy
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from travel_mode import categories, onboarding
from travel_mode.cli import main as product_main
from travel_mode.config import load_config
from travel_mode.system import shell


def validate_output(text, patterns):
    payload = text.casefold()
    if any(base64.b64decode(value).decode().casefold() in payload for value in patterns):
        raise AssertionError("Clean-machine output contains a private identifier")


def validate_snapshot(data):
    for row in data["apps"]:
        if not Path(row["path"]).is_dir():
            raise AssertionError(f"App was not observed on this machine: {row['name']}")
        rule = categories.identify(name=row["name"], bundle_id=row.get("bundle_id", ""))
        if row["rule_id"] != (rule["id"] if rule else None):
            raise AssertionError(f"Unverified app recommendation: {row['name']}")
    for row in data["services"]:
        if not Path(row["plist_path"]).is_file():
            raise AssertionError(f"Service was not observed on this machine: {row['label']}")
        rule = categories.identify(label=row["label"], executable=row.get("executable", ""))
        if rule is None and float(row["cpu"]) < load_config().health.cpu_threshold:
            raise AssertionError(f"Service has neither a rule nor high CPU evidence: {row['label']}")


def validate_discovered_paths(data):
    """Check the emitted check/suggest results, not only a second setup snapshot."""
    for row in data["apps"]:
        if not Path(row["path"]).is_dir():
            raise AssertionError(f"Output app was not observed on this machine: {row['name']}")
    services = data["services"]
    rows = services if isinstance(services, list) else services["candidates"] + services["unloadable"]
    for row in rows:
        if not Path(row["plist_path"]).is_file():
            raise AssertionError(f"Output service was not observed on this machine: {row['label']}")
    for row in data.get("with_daemon", []):
        for path in (*row["system_daemons"], *row["user_agents"]):
            if not Path(path).is_file():
                raise AssertionError(f"Output daemon was not observed on this machine: {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["TRAVEL_MODE_CONFIG"] = str(output / "config.toml")
    os.environ["TRAVEL_MODE_STATE"] = str(output / "state.json")
    patterns = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tests/unit/test_no_personal_terms.py")
    )["PRIVATE_PATTERNS"]
    original = shell.get_runner()
    shell.set_runner(shell.ReadOnlyRunner())
    try:
        assert load_config().apps.policy == "adaptive" and not load_config().apps.all
        for name, command in (
            ("check", ["check", "--json"]),
            ("setup", ["setup", "--yes", "--dry-run"]),
            ("suggest", ["suggest", "--json"]),
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = product_main([*command, "--lang", "en"])
            (output / f"{name}.stdout").write_text(stdout.getvalue())
            (output / f"{name}.stderr").write_text(stderr.getvalue())
            validate_output(stdout.getvalue() + stderr.getvalue(), patterns)
            if rc != 0:
                raise AssertionError(f"{name} exited {rc}; see captured output")
            if name != "setup":
                validate_discovered_paths(json.loads(stdout.getvalue()))
        data = onboarding.scan()
        validate_snapshot(data)
        validate_output(json.dumps(data), patterns)
        (output / "observed.json").write_text(json.dumps(data, indent=2))
        assert not (output / "config.toml").exists(), "dry-run wrote configuration"
    finally:
        shell.set_runner(original)
    print(f"PASS: clean-machine read-only acceptance; {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

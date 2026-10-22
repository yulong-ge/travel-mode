"""Capture bounded, read-only macOS parser evidence on a clean CI runner."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from travel_mode.system import shell

# No command lines (argv), user files, credentials or mutations are collected.
COMMANDS = {
    "version": ["sw_vers"],
    "pmset-current": ["pmset", "-g"],
    "pmset-custom": ["pmset", "-g", "custom"],
    "pmset-capabilities": ["pmset", "-g", "cap"],
    "pmset-battery": ["pmset", "-g", "batt"],
    "pmset-source": ["pmset", "-g", "ps"],
    "pmset-assertions": ["pmset", "-g", "assertions"],
    "pmset-log": ["pmset", "-g", "log"],
    "ioreg-battery": ["ioreg", "-rn", "AppleSmartBattery"],
    "launchctl-list": ["launchctl", "list"],
    "ps-identities": ["ps", "-axo", "pid=,ppid=,stat=,lstart=,comm="],
    "ps-health": ["ps", "-axo", "pid=,ppid=,uid=,stat=,%cpu=,lstart=,comm="],
    "sleep-times": ["sysctl", "-n", "kern.sleeptime", "kern.waketime"],
    "foreground-asn": ["lsappinfo", "front"],
    "profiler": ["system_profiler", "-json", "SPDisplaysDataType", "SPPowerDataType"],
    "backup": ["tmutil", "status"],
    "wifi-ports": ["networksetup", "-listallhardwareports"],
    "spotlight": ["mdutil", "-s", "/"],
}
ALLOWED = frozenset(tuple(command) for command in COMMANDS.values())


def collect(output, *, runner=None):
    """Preserve every result, including timeouts; return explicit failed reads."""
    output.mkdir(parents=True, exist_ok=False)
    runner = runner or shell.subprocess_runner
    results, failures = {}, []

    def capture(name, command):
        # Deliberately narrower than the product's dry-run allowlist.
        allowed = tuple(command) in ALLOWED or (
            command[:4] == ["lsappinfo", "info", "-only", "pid,bundleid"]
            and len(command) == 5
            and re.fullmatch(r"ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+:", command[-1])
        )
        if not allowed:
            raise ValueError(f"Not an allowed sample command: {command!r}")
        result = runner(command, timeout=30 if name == "profiler" else 10, interactive=False)
        (output / f"{name}.stdout").write_text(result.stdout)
        (output / f"{name}.stderr").write_text(result.stderr)
        results[name] = dict(command=command, rc=result.rc)
        if result.rc:
            failures.append(f"{name}: rc={result.rc}; {result.stderr or result.stdout}")
        return result

    for name, command in COMMANDS.items():
        result = capture(name, command)
        if name == "foreground-asn" and result.rc == 0:
            asn = result.stdout.strip()
            if re.fullmatch(r"ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+:", asn):
                capture("foreground-info", ["lsappinfo", "info", "-only", "pid,bundleid", asn])
            else:
                failures.append(f"foreground-asn: invalid ASN: {result.stdout!r}")
    (output / "manifest.json").write_text(
        json.dumps(dict(results=results, failures=failures), indent=2) + "\n"
    )
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    failures = collect(args.output)
    if failures:
        raise RuntimeError("macOS sample reads failed (raw evidence retained): " + "; ".join(failures))
    print(f"Captured read-only macOS samples: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

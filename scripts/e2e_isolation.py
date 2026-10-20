"""Fixture-only discovery for the opt-in harness, separate from full safety probes."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from e2e_guard import LABEL, PROCESS_QUERY, identities

from travel_mode import config
from travel_mode.categories import rules
from travel_mode.constants import DEFAULT_APPS
from travel_mode.system import apps, health, launchd
from travel_mode.system.shell import ReadError

APP_QUERY = ["ps", "-axo", "pid=,%cpu=,command="]
INDEX_QUERY = ["mdfind", 'kMDItemContentType == "com.apple.application-bundle"']


def write_fixture_config(root, *, graceful=None, battery_measure=False, refusing=False):
    quit_apps = (
        ["TravelModeE2E-Quit"]
        if graceful is None
        else (["TravelModeE2E-Graceful", "TravelModeE2E-Graceful-2"] if graceful else [])
    )
    skip = [] if graceful is None else ["TravelModeE2E-Quit"]
    if refusing:
        quit_apps.append("TravelModeE2E-Refusing")
    (root / "config.toml").write_text(
        '[apps]\ndefault="skip"\nwatch=[]\nask=[]\nwith_daemon=[]\n'
        + "quit="
        + json.dumps(quit_apps)
        + "\nfreeze="
        + json.dumps(
            ["TravelModeE2E-Freeze", "TravelModeE2E-Burn"] if battery_measure else ["TravelModeE2E-Freeze"]
        )
        + "\nskip="
        + json.dumps(skip)
        + "\nignore="
        + json.dumps(DEFAULT_APPS)
        + "\n[categories]\npause=[]\nignore="
        + json.dumps([row["id"] for row in rules()])
        + "\n[services]\nunload="
        + json.dumps([LABEL])
        + "\nkeep=[]\n[network]\napps=[]\n"
        + '[options]\nlanguage="en"\nscenario="ask"\naggressive=false\n'
        + ("[measurement]\nseconds=240\nupdates=3\n" if battery_measure else ""),
        encoding="utf-8",
    )


class FixtureDiscoveryRunner:
    """Project discovery reads only; GuardRunner retains full host safety observations."""

    def __init__(self, runner, root):
        self.runner = runner
        self.bundles = tuple(
            (root / f"TravelModeE2E-{name}.app").resolve()
            for name in ("Quit", "Freeze", "Graceful", "Graceful-2", "Burn", "Refusing")
        )

    def fixture_path(self, value):
        path = Path(value).resolve()
        return any(path == app or app in path.parents for app in self.bundles)

    def __call__(self, command, timeout=None, interactive=False):
        result = self.runner(command, timeout, interactive)
        if result.rc:
            return result
        lines = result.stdout.splitlines()
        if command == INDEX_QUERY:
            lines = [line for line in lines if line.strip() and self.fixture_path(line.strip())]
        elif command in (APP_QUERY, health.PROCESS_COMMAND):
            fields = 2 if command == APP_QUERY else 10
            # Preserve malformed input so the product parser still reports it.
            lines = [
                line
                for line in lines
                if len(parts := line.split(None, fields)) != fields + 1 or self.fixture_path(parts[-1])
            ]
        elif command in (["pmset", "-g", "assertions"], ["/usr/bin/pmset", "-g", "assertions"]):
            observed = self.runner(PROCESS_QUERY, timeout=5)
            if observed.rc:
                raise ReadError(f"Fixture assertion discovery: ps failed ({observed.rc}): {observed.stderr}")
            owned = {
                pid
                for pid, (_, _, executable) in identities(observed.stdout).items()
                if self.fixture_path(executable)
            }
            lines = [
                line
                for line in lines
                if not (match := re.search(r"\bpid\s+(\d+)\s*\(", line)) or int(match[1]) in owned
            ]
        elif command == ["launchctl", "list"]:
            lines = [
                line
                for line in lines
                if line.split() and (line.split()[0] == "PID" or line.split()[-1] == LABEL)
            ]
        else:
            return result
        return replace(result, stdout="\n".join(lines))


@contextmanager
def discovery_scope(root, guard):
    # Empty TOML lists do not replace these product defaults. Restrict their
    # resolved sources only for this isolated child; restore them on every exit.
    with (
        patch.object(config, "DEFAULT_APPS", ()),
        patch.object(config, "DEFAULT_NET_APPS", ()),
        patch.object(apps, "APP_DIRS", [str(root)]),
        launchd.discovery_scope((root / "home/Library/LaunchAgents",)),
    ):
        yield FixtureDiscoveryRunner(guard, root)

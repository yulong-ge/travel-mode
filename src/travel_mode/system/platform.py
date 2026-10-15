"""Platform and command capability detection."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass

from ..i18n import t
from .shell import command_path, run_out

REQUIRED_COMMANDS = (
    "pmset",
    "launchctl",
    "osascript",
    "pgrep",
    "pkill",
    "ps",
    "top",
    "networksetup",
    "mdutil",
)
OPTIONAL_COMMANDS = {
    "blueutil": t("platform.bluetooth_control"),
    "brightness": t("platform.brightness_control"),
}
# Public lowercase aliases mirror the execution plan wording.
required_commands = list(REQUIRED_COMMANDS)
optional_commands = OPTIONAL_COMMANDS


@dataclass(frozen=True)
class Capabilities:
    available: dict[str, bool]
    supported_pmset_keys: frozenset[str]

    @property
    def blueutil(self) -> bool:
        return self.available.get("blueutil", False)

    @property
    def brightness(self) -> bool:
        return self.available.get("brightness", False)


def ensure_macos() -> None:
    if sys.platform != "darwin":
        print(t("platform.travel_mode_only_supports_macos"), file=sys.stderr)
        raise SystemExit(2)


def missing_required_commands() -> list[str]:
    return [name for name in REQUIRED_COMMANDS if command_path(name) is None]


def _parse_pmset_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith(":"):
            continue
        parts = stripped.split()
        if parts and parts[0].replace("_", "").isalnum():
            keys.add(parts[0])
    return keys


def supported_pmset_keys(custom_output: str | None = None, cap_output: str | None = None) -> frozenset[str]:
    """Return keys advertised by this Mac's read-only pmset outputs."""
    if custom_output is None:
        custom_output, rc = run_out(["pmset", "-g", "custom"])
        if rc:
            raise RuntimeError(
                t("json.command_failed", command="pmset -g custom", rc=rc, detail=custom_output)
            )
    if cap_output is None:
        cap_output, rc = run_out(["pmset", "-g", "cap"])
        if rc:
            raise RuntimeError(t("json.command_failed", command="pmset -g cap", rc=rc, detail=cap_output))
    keys = _parse_pmset_keys(custom_output) | _parse_pmset_keys(cap_output)
    return frozenset(keys)


def map_pmset_key(key: str, supported: frozenset[str] | set[str]) -> str | None:
    if key == "lowpowermode":
        if "lowpowermode" in supported:
            return "lowpowermode"
        if "powermode" in supported:
            return "powermode"
        return None
    return key if key in supported else None


def filter_power_keys(keys, supported: frozenset[str] | set[str] | None = None):
    supported = supported if supported is not None else supported_pmset_keys()
    return [
        (map_pmset_key(key, supported), desc, rec)
        for key, desc, rec in keys
        if map_pmset_key(key, supported) is not None
    ]


def detect_capabilities() -> Capabilities:
    missing = missing_required_commands()
    if missing:
        raise RuntimeError(t("platform.missing_required_commands", v0=", ".join(missing)))
    available = {name: shutil.which(name) is not None for name in OPTIONAL_COMMANDS}
    from .brightness import available as brightness_available

    available["brightness"] = brightness_available()
    return Capabilities(available, supported_pmset_keys())


__all__ = [
    "OPTIONAL_COMMANDS",
    "REQUIRED_COMMANDS",
    "Capabilities",
    "detect_capabilities",
    "ensure_macos",
    "filter_power_keys",
    "map_pmset_key",
    "missing_required_commands",
    "optional_commands",
    "required_commands",
    "supported_pmset_keys",
]

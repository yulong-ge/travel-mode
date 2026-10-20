"""Safety-only contracts; aliases share expectations, never scenario execution."""

import json
from pathlib import Path

FIELDS = {"commands", "state", "exit_code"}
ALIASES = {
    "cli-config-set-language-zh": "cli-config-init---force",
    "config-init-new": "cli-config-init---force",
    "cli-config-validate": "cli-config-path",
    "cli-report": "cli-report---json",
    "cli-suggest": "cli-suggest---json",
    "thaw-accept": "cli-thaw---all",
    "thaw-reject": "cli-thaw",
    "off-force": "off-complete",
    "tui-network-daemon": "tui-daemon",
    "tui-dashboard-zh": "tui-dashboard-en",
    "tui-language": "tui-dashboard-en",
    "tui-skip": "tui-dashboard-en",
    "tui-spotlight": "tui-service-spotlight",
}


def canonical_name(name):
    return ALIASES.get(name, name)


def validate_contract(data, path):
    if not isinstance(data, dict) or data.keys() != FIELDS:
        raise ValueError(f"{path}: contract requires exactly commands, state and exit_code")
    if not isinstance(data["commands"], list) or any(
        not isinstance(cmd, list) or not cmd or any(not isinstance(arg, str) for arg in cmd)
        for cmd in data["commands"]
    ):
        raise ValueError(f"{path}: commands must be ordered, nonempty argv lists")
    if data["state"] is not None and not isinstance(data["state"], dict):
        raise ValueError(f"{path}: state must be a complete object or null")
    if data["exit_code"] is not None and type(data["exit_code"]) is not int:
        raise ValueError(f"{path}: exit_code must be an integer or null for TUI")
    return data


def load_contract(path: Path):
    return validate_contract(json.loads(path.read_text()), path)

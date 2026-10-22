"""The two observed lsappinfo renderings share one strict foreground identity parser."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

from ..i18n import t
from .shell import ReadError, _read


class ForegroundError(ReadError):
    """Foreground identity could not be established; app mutations must stop."""


@dataclass(frozen=True)
class Foreground:
    pid: int
    bundle_id: str | None
    bundle_path: str | None = None
    source: str = "lsappinfo"
    fallback_reason: str | None = None

    def as_dict(self):
        from dataclasses import asdict

        return asdict(self)


def parse_info(raw):
    # Output keys differ between the observed display and CFString renderings.
    # Only these two spellings are supported; diagnostics retain unknown input.
    pids = re.findall(r'^\s*(?:pid|"pid")\s*=\s*(\d+)(?=\s|$)', raw, re.MULTILINE)
    pid_lines = re.findall(r'^\s*(?:pid|"pid")\s*=', raw, re.MULTILINE)
    identifiers = re.findall(
        r'^\s*(?:bundleID|"CFBundleIdentifier")\s*=\s*"([^"\n]+)"\s*$', raw, re.MULTILINE
    )
    identifier_lines = re.findall(r'^\s*(?:bundleID|"CFBundleIdentifier")\s*=', raw, re.MULTILINE)
    if (
        len(pids) != 1
        or len(pid_lines) != 1
        or int(pids[0]) <= 0
        or len(identifiers) != len(identifier_lines)
        or len(identifiers) > 1
    ):
        raise ForegroundError(t("setup.foreground_error", detail=repr(raw)))
    return Foreground(int(pids[0]), identifiers[0] if identifiers else None)


def foreground(read=None):
    reader = read or (lambda command: _read(command, timeout=2))
    try:
        raw = reader([sys.executable, "-m", "travel_mode.system.health_native", "foreground"])
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(f"NSWorkspace: expected object: {raw!r}")
        if row.get("status") != "available":
            raise ValueError(f"NSWorkspace unavailable: {row!r}")
        if type(row.get("pid")) is not int or row["pid"] <= 0:
            raise ValueError(f"NSWorkspace: invalid PID: {raw!r}")
        for key in ("bundle_id", "bundle_path"):
            if key not in row or (row[key] is not None and not isinstance(row[key], str)):
                raise ValueError(f"NSWorkspace: invalid {key}: {raw!r}")
        return Foreground(row["pid"], row["bundle_id"], row["bundle_path"], source="NSWorkspace")
    except (ReadError, ValueError) as exc:
        native_error = str(exc)
    try:
        front = reader(["lsappinfo", "front"]).strip()
        if not re.fullmatch(r"ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+:", front):
            raise ForegroundError(t("setup.foreground_error", detail=repr(front)))
        parsed = parse_info(reader(["lsappinfo", "info", "-only", "pid,bundleid", front]))
        return Foreground(parsed.pid, parsed.bundle_id, fallback_reason=native_error)
    except ReadError as exc:
        raise ForegroundError(
            t("setup.foreground_error", detail=f"{native_error}; lsappinfo: {exc}")
        ) from exc

"""The two observed lsappinfo renderings share one strict foreground identity parser."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..i18n import t
from .shell import ReadError, _read


class ForegroundError(ReadError):
    """Foreground identity could not be established; app mutations must stop."""


@dataclass(frozen=True)
class Foreground:
    pid: int
    bundle_id: str | None


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
    reader = read or (lambda command: _read(command, timeout=5))
    try:
        front = reader(["lsappinfo", "front"]).strip()
        if not re.fullmatch(r"ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+:", front):
            raise ForegroundError(t("setup.foreground_error", detail=repr(front)))
        return parse_info(reader(["lsappinfo", "info", "-only", "pid,bundleid", front]))
    except ForegroundError:
        raise
    except ReadError as exc:
        raise ForegroundError(t("setup.foreground_error", detail=str(exc))) from exc

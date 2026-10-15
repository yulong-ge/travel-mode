"""System adapter: network."""

from __future__ import annotations

import re

from ..i18n import t
from .shell import _read


def wifi_dev():
    out = _read(["networksetup", "-listallhardwareports"], timeout=15)
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if ("Wi-Fi" in line or "AirPort" in line) and i + 1 < len(lines):
            m = re.search(r"Device:\s*(\S+)", lines[i + 1])
            if m:
                return m.group(1)
    return ""


def wifi_powered(dev):
    out = _read(["networksetup", "-getairportpower", dev])
    match = re.search(r":\s*(On|Off)\s*$", out)
    if not match:
        raise RuntimeError(
            t("safety.invalid_status", command=f"networksetup -getairportpower {dev}", detail=out.strip())
        )
    return match[1] == "On"


def bt_powered():
    out = _read(["blueutil", "-p"])
    if out.strip() not in {"0", "1"}:
        raise RuntimeError(t("safety.invalid_status", command="blueutil -p", detail=out.strip()))
    return out.strip() == "1"

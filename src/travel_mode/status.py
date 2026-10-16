"""Cheap menu status: local files and a single bounded power-source query."""

import json
import os
import time

from .i18n import get_language, t
from .state import load_state
from .system.shell import _read

SOURCE_TIMEOUT = 2


class StatusError(RuntimeError):
    """A status observation failed."""


def source():
    # Do not call capability detection, migration, snapshots, health or discovery.
    raw = _read(["pmset", "-g", "ps"], timeout=SOURCE_TIMEOUT)
    for name, value in (("AC Power", "ac"), ("Battery Power", "battery"), ("UPS Power", "ups")):
        if name in raw:
            return value
    raise StatusError(t("automation.unknown_source", detail=raw.strip()))


def data(*, clock=time.monotonic):
    started = clock()
    snapshot = load_state()
    result = dict(
        version=1,
        active=snapshot is not None,
        scenario=(snapshot.get("scenario") or None) if snapshot else None,
        started_at=(
            snapshot.get("session", {}).get("started_at")
            or snapshot.get("anchored_at")
            or snapshot.get("saved_at")
            or None
        )
        if snapshot
        else None,
        power_source=source(),
        language=get_language(),
        source_timeout_seconds=SOURCE_TIMEOUT,
    )
    result["swiftbar"] = render(result)
    result["elapsed_seconds"] = clock() - started
    return result


def render(result):
    executable = os.environ.get("TRAVEL_MODE_BIN", "travel-mode")
    if any(char in executable for char in "\n\r|\x00"):
        raise StatusError(t("menubar.invalid_executable"))
    quoted = json.dumps(executable, ensure_ascii=False)

    def clean(value):
        return " ".join(str(value or "—").split())[:160]

    rows = ["✈️" if result["active"] else "🔋", "---"]
    rows.append(
        t(
            "menubar.active" if result["active"] else "menubar.inactive",
            scenario=clean(result["scenario"]),
            started=clean(result["started_at"]),
        ).replace("|", "/")
    )
    rows.append(t("menubar.source", source=t("menubar.source_" + result["power_source"])))
    rows.append("---")
    for key, arguments in (
        ("work", ("--scenario", "work")),
        ("away", ("--scenario", "away")),
        ("off", ("off",)),
        ("check", ("check",)),
        ("power", ("power",)),
        ("history", ("history",)),
        ("automation", ("automation", "status")),
    ):
        params = " ".join(f"param{index}={json.dumps(arg)}" for index, arg in enumerate(arguments, 1))
        rows.append(f"{t('menubar.' + key)} | bash={quoted} {params} terminal=true refresh=true")
    return "\n".join(rows)

"""flow_thaw implementation."""

from __future__ import annotations

import sys

from travel_mode.actions.apps import ResumeProcess
from travel_mode.actions.executor import execute
from travel_mode.i18n import t
from travel_mode.system.shell import run_out

"""Emergency thaw flow for stopped application processes."""


def thaw_processes(all_processes=False):
    """List stopped app processes and resume selected pids with SIGCONT."""
    out, rc = run_out(["ps", "-axo", "pid,stat,comm"])
    if rc != 0:
        print(t("impl.reading_stopped_processes_failed_rc", v0=rc), file=sys.stderr)
        return 1
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].startswith("T"):
            continue
        if ".app/Contents/" not in parts[2]:
            continue
        rows.append((parts[0], parts[2]))
    if not rows:
        print(t("impl.no_stopped_app_processes_found"))
        return 0
    for pid, command in rows:
        print(t("impl.stopped_process", v0=pid, v1=command))
    selected = rows
    if not all_processes:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print(t("impl.use_travel_mode_thaw_all_in"), file=sys.stderr)
            return 1
        answer = input(t("impl.thaw_these_processes")).strip().lower()
        if answer not in {"y", "yes"}:
            return 1
    fails = 0
    for pid, _ in selected:
        _, result = execute(ResumeProcess(pid))
        if result == 0:
            print(t("impl.thawed_2", v0=pid))
        else:
            print(t("impl.thaw_failed_for_rc", v0=pid, v1=result), file=sys.stderr)
            fails += 1
    return 1 if fails else 0

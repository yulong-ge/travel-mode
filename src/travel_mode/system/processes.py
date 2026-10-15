"""System adapter: processes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..i18n import t
from . import pmset, process_control
from .shell import run_out, run_rc


def pgrep_lines(pattern):
    """pgrep -fl, 返回 "pid cmd" 行列表."""
    out, rc = run_rc(["pgrep", "-fl", pattern], timeout=10)
    if rc not in (0, 1):
        raise RuntimeError(t("json.command_failed", command=f"pgrep {pattern!r}", rc=rc, detail=out))
    return [line for line in out.splitlines() if line.strip()]


def pgrep_alive(pattern):
    return len(pgrep_lines(pattern)) > 0


def first_line(out):
    return out.splitlines()[0] if out else ""


@dataclass(frozen=True)
class CaffeinateTarget:
    process: process_control.Process
    command_line: str
    parent: str

    def describe(self):
        return t(
            "caffeinate.target",
            pid=self.process.pid,
            command=self.command_line,
            ppid=self.process.ppid,
            parent=self.parent,
        )


def caffeinate_procs():
    """Only assertion holders whose executable basename is exactly caffeinate."""
    owners = {
        int(match[1])
        for line in pmset.pmset_assertions()
        if re.search(r"\bPrevent(?:UserIdle)?(?:System|Display)Sleep\b", line)
        and (match := re.search(r"\bpid\s+(\d+)\s*\([^)]+\)", line))
    }
    if not owners:
        return []
    rows = process_control.process_table()
    targets = [row for row in rows if row.pid in owners and Path(row.command).name == "caffeinate"]
    if not targets:
        return []
    output, rc = run_rc(["ps", "-ww", "-axo", "pid=,command="], timeout=10)
    if rc:
        raise RuntimeError(t("json.command_failed", command="ps full command lines", rc=rc, detail=output))
    arguments = {}
    for line in output.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            raise ValueError(t("safety.process_row_invalid", row=repr(line)))
        arguments[int(fields[0])] = fields[1]
    by_pid = {row.pid: row for row in rows}
    result = []
    for row in targets:
        if row.pid not in arguments:
            raise ProcessLookupError(t("caffeinate.changed", pid=row.pid))
        parent = by_pid.get(row.ppid)
        result.append(
            CaffeinateTarget(
                row, arguments[row.pid], parent.command if parent else t("caffeinate.parent_exited")
            )
        )
    return result


def terminate_caffeinate(target):
    if not isinstance(target, CaffeinateTarget) or Path(target.process.command).name != "caffeinate":
        raise ValueError(t("caffeinate.invalid_target"))
    # Recheck assertion ownership, then validate birth time and executable before signaling.
    owners = pmset.pmset_assertions()
    if not any(
        re.search(rf"\bpid\s+{target.process.pid}\s*\([^)]+\)", line)
        and re.search(r"\bPrevent(?:UserIdle)?(?:System|Display)Sleep\b", line)
        for line in owners
    ) or not process_control._signal(target.process, "-TERM"):
        raise ProcessLookupError(t("caffeinate.changed", pid=target.process.pid))
    return "", 0


def top_memcpu_lines():
    out, _ = run_out(["top", "-l", "1", "-n", "0"], timeout=30)
    return [line.strip() for line in out.splitlines() if "CPU usage" in line or "PhysMem" in line]


def power_top(n=5):
    """top -o power 第 2 次采样的 top n -> [(power, cmd)]."""
    out, _ = run_out(
        ["top", "-l", "2", "-stats", "pid,power,command", "-o", "power", "-n", str(n)], timeout=60
    )
    rows = []
    for line in out.splitlines():
        p = line.split(None, 2)
        if len(p) == 3 and p[0].isdigit():
            rows.append((p[1], p[2].strip()))
    return rows[-n:]


def ps_cpu_for(name, path=""):
    out, _ = run_out(["ps", "-axo", "%cpu,comm", "-r"], timeout=15)
    frag = (path + "/") if path else ""
    for line in out.splitlines():
        p = line.strip().split(None, 1)
        if len(p) == 2 and (name in p[1] or (frag and frag in p[1])):
            return p[0]
    return ""

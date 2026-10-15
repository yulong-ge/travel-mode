"""flow_report implementation."""

from __future__ import annotations

import re
from collections import Counter

from travel_mode.actions import runtime
from travel_mode.i18n import t
from travel_mode.system.shell import run_out


def parse_sleep_segments(text):
    """解析 pmset 日志中的电池睡眠段，返回结构化记录列表。"""
    segments = []
    in_sleep = False
    start_charge = start_ts = last_charge = last_ts = None
    for line in text.splitlines():
        if "Using Batt" not in line:
            continue
        match = re.search(r"Charge:\s*(\d+)", line)
        if not match:
            continue
        charge, timestamp = int(match.group(1)), line[:19]
        if any(k in line for k in ("Entering Sleep", "Clamshell Sleep", "Maintenance Sleep")):
            if not in_sleep:
                start_charge, start_ts, in_sleep = charge, timestamp, True
            last_charge, last_ts = charge, timestamp
        elif "Assertions" in line and "Summary" in line:
            if in_sleep and last_charge is not None and last_charge < start_charge:
                segments.append(
                    {
                        "start": start_ts,
                        "end": last_ts,
                        "start_charge": start_charge,
                        "end_charge": last_charge,
                        "drain": start_charge - last_charge,
                    }
                )
            in_sleep = False
    return segments


def parse_darkwake_reasons(text):
    """统计电池段 DarkWake 原因。"""
    reasons = Counter()
    for line in text.splitlines():
        if "DarkWake" in line and "BATT" in line:
            match = re.search(r"due to ([^(]*)", line)
            if match:
                reason = match.group(1).replace("BATT", "").split("/")[0].strip()
                reasons[reason] += 1
    return dict(reasons)


def mode_report():
    from rich.console import Console
    from rich.table import Table

    c = Console()
    c.rule(t("impl.overnight_last_lid_close_battery_report"))
    out, _ = run_out(["pmset", "-g", "log"], timeout=30)
    segs = []
    in_sleep = s_ch = s_ts = last_ch = last_ts = None
    for line in out.splitlines():
        if "Using Batt" not in line:
            continue
        m = re.search(r"Charge:\s*(\d+)", line)
        if not m:
            continue
        ch, ts = int(m.group(1)), line[:19]
        if any(k in line for k in ("Entering Sleep", "Clamshell Sleep", "Maintenance Sleep")):
            if not in_sleep:
                s_ch, s_ts, in_sleep = ch, ts, True
            last_ch, last_ts = ch, ts
        elif "Assertions" in line and "Summary" in line:
            if in_sleep and last_ch is not None and last_ch < s_ch:
                segs.append(t("impl.discharge", v0=s_ts, v1=last_ts, v2=s_ch, v3=last_ch, v4=s_ch - last_ch))
            in_sleep = None
    for s in segs[-15:]:
        c.print(s)
    c.print()
    c.print(t("impl.darkwake_reasons_battery_segments"))
    reasons = Counter()
    for line in out.splitlines():
        if "DarkWake" in line and "BATT" in line:
            m = re.search(r"due to ([^(]*)", line)
            if m:
                reasons[m.group(1).split("/")[0].strip()] += 1
    table = Table(show_header=False, box=None, padding=(0, 1))
    for reason, cnt in reasons.most_common(8):
        table.add_row(f"{cnt:3d}×", reason)
    c.print(table)
    c.print(t("impl.tip_if_darkwake_exceeds_min_with"))
    runtime._finish_dry_run()
    return 0

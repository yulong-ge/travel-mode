"""snapshots implementation."""

from __future__ import annotations

from datetime import datetime

from travel_mode import state as state_api
from travel_mode.paths import state_path
from travel_mode.system import pmset


def load_state():
    from .state import load_state as _load

    return _load(path=state_path())


def snapshot_origin():
    """取"开启前"快照基底. 已有快照则沿用其原始值并合并变更列表."""
    prev = load_state()
    if prev:
        return prev, True
    st = state_api.new_state()
    profs = pmset.pmset_custom()
    st["power_origin"] = {
        field: state_api.profile_string(profs, domain)
        for field, domain in (("batt_prof", "b"), ("ac_prof", "a"))
        if profs.get(domain)
    }
    sleep = pmset.sleep_disabled()
    if sleep is not None:
        st["power_origin"]["sleepdisabled"] = sleep
    else:
        st.pop("sleepdisabled", None)
    from .profiles import active_name, active_scenario

    st["profile"] = active_name()
    if active_scenario() != "ask":
        st["scenario"] = active_scenario()
    return st, False


def anchor_age_hours(st):
    """锚点 (首次会话) 距今小时数. 旧快照无 anchored_at 时退回 saved_at."""
    for k in ("anchored_at", "saved_at"):
        try:
            t = datetime.strptime(st[k], "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - t).total_seconds() / 3600
        except (KeyError, ValueError):
            continue
    return None

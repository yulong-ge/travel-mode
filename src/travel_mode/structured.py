"""只读结构化输出，stdout 仅由 CLI 输出 JSON。"""

from __future__ import annotations

import re

from travel_mode.system import apps as system_apps
from travel_mode.system import launchd, pmset, processes, spotlight

from . import __version__
from .config import load_config
from .flows.doctor import collect
from .i18n import t
from .state import load_state
from .system.platform import map_pmset_key, supported_pmset_keys
from .system.shell import ReadError, optional_tool_paths, run_rc


def read_command(command: list[str], timeout=30) -> str:
    output, rc = run_rc(command, timeout=timeout)
    if rc:
        raise ReadError(t("json.command_failed", command=" ".join(command), rc=rc, detail=output))
    return output


def check_data() -> dict:
    report = collect()
    from .health_view import presentation

    report["display_checks"] = presentation(report, scenario=load_config().options.scenario)
    try:
        result = _audit_data()
    except ReadError as exc:
        # No fabricated legacy values: the incomplete audit is explicit and the
        # nine independent health results are still returned in full.
        result = {"version": __version__.split(".dev", 1)[0], "audit_error": str(exc)}
    return {**result, "health": report, "tool_paths": optional_tool_paths()}


def _audit_data() -> dict:
    raw = read_command(["pmset", "-g", "batt"]).strip()
    match = re.search(r"(\d+)%;", raw)
    source = "ac" if pmset.on_ac_power() else "battery"
    sd = pmset.sleep_disabled()
    blockers = [
        {
            "level": "warn" if sd is None else "bad" if sd == "1" else "ok",
            "kind": "sleep_disabled",
            "message": t("power.sleep_switch_unavailable")
            if sd is None
            else f"SleepDisabled={pmset.sleep_disabled()}",
        }
    ]
    for target in processes.caffeinate_procs():
        blockers.append(
            {"level": "warn", "kind": "caffeinate", "message": target.describe(), "pid": target.process.pid}
        )
    for row in pmset.configured_sleep_blocking_assertions():
        if not row["pid"]:
            continue  # 系统汇总计数不属于进程持有者。
        blockers.append(
            {"level": "warn", "kind": "assertion", "message": row["line"], "pid": int(row["pid"])}
        )
    audio = pmset.audio_assertion()
    if audio:
        blockers.append({"level": "warn", "kind": "audio", "message": audio})
    supported = supported_pmset_keys()
    config = load_config()
    current = pmset.pmset_custom().get("b", {})
    supported = supported & current.keys()
    power = []
    for key, _, recommended in pmset.POWER_KEYS:
        actual = map_pmset_key(key, supported)
        power.append(
            {
                "key": key,
                "current": current.get(actual),
                "recommended": config.power_overrides.get(key, recommended),
                "supported": actual is not None and actual in current,
                "description": t(f"power.{key}.desc"),
                "values": t(f"power.{key}.values"),
                "effect": t(f"power.{key}.effect"),
            }
        )
    app_scan = system_apps.scan_apps()
    apps = [
        {
            "name": app["name"],
            "path": app["path"],
            "cpu": float(app["cpu"]) if app["cpu"] else None,
            "default_action": app["defact"],
        }
        for app in app_scan["running"]
    ]
    services = launchd.svc_scan()
    state = load_state()
    from .resurrection import detect

    revived = detect(state)
    if state and "session" in state:
        from .sessions import note

        note(state, revived, external=pmset.on_ac_power())

    return {
        **({"foreground": app_scan["foreground"]} if "foreground" in app_scan else {}),
        **({"app_safety": app_scan["app_safety"]} if "app_safety" in app_scan else {}),
        "version": __version__.split(".dev", 1)[0],
        "scenario": state.get("scenario") or None if state else None,
        "battery": {"percent": int(match[1]) if match else None, "source": source, "raw": raw},
        "blockers": blockers,
        "resurrected": revived,
        "power": power,
        "apps": apps,
        "services": {key: services[key] for key in ("unloadable", "candidates", "kept")},
        "spotlight_indexing": spotlight.spotlight_on(),
        "snapshot": {
            "exists": state is not None,
            "anchored_at": state.get("anchored_at", "") if state else None,
            "profile": state.get("profile", "") if state else None,
        },
    }


def report_data() -> dict:
    from .flows.report import parse_darkwake_reasons, parse_sleep_segments

    output = read_command(["pmset", "-g", "log"])
    result = {
        "version": __version__.split(".dev", 1)[0],
        "sleep_segments": parse_sleep_segments(output),
        "darkwake_reasons": parse_darkwake_reasons(output),
    }
    state = load_state()
    if state and "measurement" in state:
        result["measurement"] = state["measurement"]
    return result

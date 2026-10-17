"""flow_check implementation."""

from __future__ import annotations

from travel_mode.i18n import t
from travel_mode.system import apps as system_apps
from travel_mode.system import launchd, pmset, processes, spotlight


def audit_lines():
    lines = [pmset.battery_line()] + [f"  {line}" for line in processes.top_memcpu_lines()]
    lines.append(
        t("impl.ac_connected_battery_profile_takes_effect")
        if pmset.on_ac_power()
        else t("impl.running_on_battery")
    )
    lines.append(t("impl.top_energy_consumers"))
    for pw, cmd in processes.power_top():
        lines.append(f"    {pw:>6}  {cmd}")
    return lines


class BlockerLines(list):
    """Presentation lines plus the exact identities used to build their controls."""

    def __init__(self, targets, lines=()):
        super().__init__(lines)
        self.caffeinate_targets = tuple(targets)


def blockers_lines():
    lines = BlockerLines(processes.caffeinate_procs())
    sd = pmset.sleep_disabled()
    lines.append(
        ("bad", t("impl.sleepdisabled_sleep_disabled_globally_closing_the", v0=sd))
        if sd == "1"
        else ("dim", t("power.sleep_switch_unavailable"))
        if sd is None
        else ("ok", "SleepDisabled=0")
    )
    for target in lines.caffeinate_targets:
        lines.append(("warn", t("impl.caffeinate_prevents_sleep", v0=target.describe())))
    rows = pmset.configured_sleep_blocking_assertions()
    if rows:
        for row in rows:
            detail = row.get("named") or row.get("process") or row["line"]
            suffix = t("impl.this_app_is_kept_alive_by") if row.get("daemon_app") else ""
            lines.append(("warn", f"{row['type']}: {detail}{suffix}"))
    else:
        lines.append(("ok", t("impl.no_system_sleep_blocking_assertions")))
    au = pmset.audio_assertion()
    if au:
        lines.append(("warn", t("impl.audio_assertion_quit_the_app_playing", v0=au.split("named: ")[-1])))
    else:
        lines.append(("ok", t("impl.no_audio_assertions")))
    return lines


def mode_check():
    from rich.console import Console

    from ..system.shell import ReadError
    from .doctor import collect, lines

    report = collect()
    from ..system.shell import optional_tool_paths

    console = Console()
    console.rule(t("shell.optional_tools"))
    for tool, path in optional_tool_paths().items():
        console.print(t("shell.tool_path", tool=tool, path=path or t("shell.not_found")), markup=False)
    try:
        _mode_check()
    except ReadError as exc:
        Console().print(t("health.legacy_failed", reason=str(exc)), markup=False, highlight=False)
    console.rule(t("health.title"))
    for line in lines(report):
        console.print(line, markup=False, highlight=False)
    return 0


def _mode_check():
    from rich.console import Console

    c = Console()

    def emit(kind, msg):
        mark = {"ok": "[green]✓[/]", "warn": "[yellow]![/]", "bad": "[red]✗[/]", "dim": "[dim]·[/]"}[kind]
        c.print(f"{mark} {msg}")

    c.rule(t("impl.system_audit"))
    from ..resurrection import describe, detect
    from ..state import load_state

    state = load_state()
    rows = detect(state)
    if state and "session" in state:
        from ..sessions import note

        note(state, rows, external=pmset.on_ac_power())
    for row in rows:
        emit("warn", describe(row))
    if load_state():
        emit("dim", t("resurrection.hint"))
    for line in audit_lines():
        c.print(line, highlight=False)
    c.rule(t("impl.power_settings_battery_profile"))
    profs = pmset.pmset_custom()
    from ..system.platform import map_pmset_key, supported_pmset_keys

    supported = supported_pmset_keys() & profs.get("b", {}).keys()
    for key, _desc, rec in pmset.POWER_KEYS:
        actual = map_pmset_key(key, supported)
        cur = pmset.pmget(actual, "b", profs) if actual else None
        cur = cur or t("power.unsupported")
        mark = "green" if cur == rec else "yellow"
        detail = " ".join(
            t("power.detail_line", heading=t(f"power.heading.{part}"), detail=t(f"power.{key}.{part}"))
            for part in ("desc", "values", "effect")
        )
        c.print(t("impl.current_recommended", v0=mark, v1=key, v2=cur, v3=rec, v4=detail))
    c.rule(t("impl.high_energy_gui_apps"))
    scan = system_apps.scan_apps()
    for app in scan["running"]:
        emit(
            "warn",
            t("impl.running_cpu_default_action", v0=app["name"], v1=app["cpu"] or "?", v2=app["defact"]),
        )
    if not scan["running"]:
        emit("ok", t("impl.no_listed_high_energy_apps_are"))
    for msg in scan["nobundle"]:
        emit("warn", msg)
    c.rule(t("impl.network_apps"))
    from ..network_apps import network_description, network_scan

    for row in network_scan():
        emit("warn" if row["running"] else "dim", network_description(row))
    c.rule(t("impl.background_agents"))
    svc = launchd.svc_scan()
    for s in svc["unloadable"]:
        emit("warn", t("impl.loaded_agent_pid_cpu", v0=s["label"], v1=s["pid"], v2=s["cpu"] or "?"))
    if spotlight.spotlight_on():
        emit("warn", t("impl.spotlight_indexing_enabled_mds_mdworker"))
    else:
        emit("ok", t("impl.spotlight_indexing_already_disabled"))
    for k in svc["kept"]:
        emit("dim", t("impl.kept", v0=k))
    from ..system.launchd import service_information

    for info in service_information(svc):
        emit("dim", info)
    for candidate in svc.get("candidates", []):
        emit("dim", f"{candidate['label']} (candidate, pid {candidate['pid']})")
    return 0


def reapply_paused():
    from ..actions.base import Context
    from ..actions.executor import Executor, _sudo_lifecycle
    from ..failures import batch
    from ..resurrection import reapply
    from ..state import load_state, save_state

    @_sudo_lifecycle
    def apply():
        state = load_state()
        if state is not None:
            executor = Executor(Context(state, save_state))
            with batch():
                return reapply(state, executor.execute, save_state)
        return 0

    return apply()

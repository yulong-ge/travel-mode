"""flow_apply implementation."""

from __future__ import annotations

import re
import shutil

from travel_mode import config as config_api
from travel_mode import network_apps as network_api
from travel_mode import snapshots
from travel_mode import state as state_api
from travel_mode.actions import runtime
from travel_mode.actions.apps import (
    FreezeApp,
    QuitApp,
    QuitConfiguredDaemons,
    QuitDaemonApp,
    UnloadAgent,
    configured_daemon,
)
from travel_mode.actions.executor import execute
from travel_mode.actions.network import BluetoothOff, WifiOff
from travel_mode.actions.power import (
    DisplaySleepNow,
    EnableSleep,
    KillCaffeinate,
    SetBrightness,
    SetPowerProfile,
    StopSpotlight,
)
from travel_mode.constants import LIST_KEYS
from travel_mode.flows import common
from travel_mode.presentation import action_message as t
from travel_mode.system import apps as system_apps
from travel_mode.system import brightness, launchd, pmset, processes, spotlight
from travel_mode.system import network as system_network
from travel_mode.system.shell import is_dry_run


def ask(prompt, default="y", yes=False, *, risk="safe", aggressive=False, emit=None):
    if risk == "aggressive" and yes and not aggressive:
        if emit is not None:
            emit("dim", t("impl.skipped_aggressive_action_use_aggressive_to", v0=prompt))
        return False
    if yes:
        return True
    if risk == "aggressive":
        prompt = f"⚠ {prompt}"
        default = "n"
    hint = "[Y/n]" if default == "y" else "[y/N]"
    try:
        ans = input(f"  ? {prompt} {hint} ").strip().lower()
    except EOFError:
        ans = ""
    return (ans or default) in ("y", "yes")


def ask_qf(prompt, default_act, yes=False, *, aggressive=False):
    """三选: 返回 quit|freeze|skip. yes 模式按配置默认执行."""
    if yes:
        return default_act
    hy = "Y" if default_act == "quit" else "y"
    hf = "F" if default_act == "freeze" else "f"
    try:
        ans = input(t("impl.quit_freeze_skip", v0=prompt, v1=hy, v2=hf)).strip().lower()
    except EOFError:
        ans = ""
    if not ans:
        return default_act
    if ans in ("f", "z", "freeze"):
        return "freeze"
    if ans in ("n", "no", "s", "skip"):
        return "skip"
    return "quit"


@runtime._sudo_lifecycle
def mode_apply(yes=False, aggressive=False, dry_run=False, measure=None):
    runtime._prepare_dry_run(dry_run)
    from ..profiles import active_values

    config = config_api.load_config()
    profile = active_values()
    work = config.options.scenario == "work"
    from .setup import Remember

    remember = Remember()
    aggressive = aggressive or config.options.aggressive
    profile_disconnect = profile.get("network", {}).get("disconnect", False)
    profile_wifi_off = profile.get("wifi") == "off"
    profile_bluetooth_off = profile.get("bluetooth") == "off"
    profile_spotlight_off = profile.get("spotlight") == "off"
    profile_brightness = profile.get("brightness")
    from rich.console import Console
    from rich.markup import escape

    c = Console()
    if not yes:
        print(t("cli.prompt_help"))

    def emit(kind, msg):
        mark = {"ok": "[green]✓[/]", "warn": "[yellow]![/]", "bad": "[red]✗[/]", "dim": "[dim]·[/]"}[kind]
        c.print(f"{mark} {msg}")

    st, merged = snapshots.snapshot_origin()
    from .. import measurement

    before = measurement.begin(
        config.options.scenario, measure, config.measurement, lambda msg: emit("dim", escape(msg))
    )
    if config.options.scenario != "ask":
        st["scenario"] = config.options.scenario
    from ..actions.journal import mutation_scope
    from ..failures import batch, reporter

    with mutation_scope(st), reporter(emit), batch(emit):
        if merged:
            age = snapshots.anchor_age_hours(st)
            age_s = t("impl.hours_ago", v0=age) if age is not None else ""
            emit("warn", t("impl.existing_snapshot_keeping_original_values_and", v0=age_s))
            if age is not None and age > 24:
                emit("warn", t("impl.old_snapshot_off_rolls_back_to"))
        if system_apps.ensure_conf() and not yes:
            emit("dim", t("impl.created_default_action_configuration_editable", v0=common.CONF_PATH))

        fixsleep_applied = False
        if not work:
            c.rule(t("impl.sleep_blockers_highest_priority"))
            sd = pmset.sleep_disabled()
            if sd is None:
                emit("dim", t("power.sleep_switch_unavailable"))
            elif sd == "1":
                emit("bad", t("impl.sleepdisabled_sleep_disabled_globally_closing_the_2"))
                if ask(t("impl.fix_pmset_disablesleep"), yes=yes):
                    _, rc = execute(EnableSleep())
                    fixsleep_applied = rc == 0
                    emit(
                        "ok" if rc == 0 else "warn",
                        t("impl.sleep_enabled") if rc == 0 else t("impl.fixing_disablesleep_failed"),
                    )
                else:
                    emit("warn", t("impl.keeping_sleep_disabled"))
            else:
                emit("ok", "SleepDisabled=0")
            for target in processes.caffeinate_procs():
                emit("warn", t("impl.caffeinate_prevents_sleep_2", v0=target.describe()))
                if ask(
                    t("caffeinate.confirm", pid=target.process.pid),
                    yes=yes,
                    risk="aggressive",
                    aggressive=aggressive,
                    emit=emit,
                ):
                    _, terminated_rc = execute(KillCaffeinate(target))
                    emit(
                        "ok" if terminated_rc == 0 else "bad",
                        t("impl.terminated") if terminated_rc == 0 else t("failure.incomplete"),
                    )
            rows = pmset.configured_sleep_blocking_assertions()
            if rows:
                emit("warn", t("impl.system_sleep_blocking_assertions_found"))
                if ask(t("impl.quit_the_configured_daemon_app"), yes=yes):
                    _, okq, msg = execute(QuitConfiguredDaemons(st))
                    emit("ok" if okq else "warn", t("impl.daemon_app", v0=msg))
            else:
                emit("ok", t("impl.no_system_sleep_blocking_assertions"))
            au = pmset.audio_assertion()
            if au:
                emit("warn", t("impl.audio_assertion_quit_the_app_playing", v0=au.split("named: ")[-1]))
            else:
                emit("ok", t("impl.no_audio_assertions"))

        c.rule(t("impl.power_settings_battery_profile"))
        profs = pmset.pmset_custom()
        if not profs.get("b"):
            emit("dim", t("power.battery_unavailable"))
        chg = []
        power_applied = False
        for key, desc, rec in pmset.power_recommendations(profs):
            cur = pmset.pmget(key, "b", profs) or "(unset)"
            if cur == rec:
                emit("ok", t("impl.is_already", v0=key, v1=cur, v2=desc))
                continue
            if ask(t("impl.set_current_recommended", v0=key, v1=rec, v2=cur, v3=rec, v4=desc), yes=yes):
                chg += [key, rec]
        if (
            not work
            and "hibernatemode" in profs.get("b", {})
            and ask(
                t("impl.use_hibernatemode_write_ram_to_disk"),
                default="n",
                yes=yes,
                risk="aggressive",
                aggressive=aggressive,
                emit=emit,
            )
        ):
            for i in range(0, len(chg), 2):
                if chg[i] == "hibernatemode":
                    chg[i + 1] = "25"
                    break
            else:
                chg += ["hibernatemode", "25"]
        if chg:
            # 一次 pmset 原子写入, 避免逐项写出现 displaysleep>sleep 中间态
            _, rc = execute(SetPowerProfile([*chg]))
            if rc == 0:
                power_applied = True
                for i in range(0, len(chg), 2):
                    emit("ok", f"{chg[i]} → {chg[i + 1]}")
                newb = (
                    {**profs.get("b", {}), **dict(zip(chg[::2], chg[1::2], strict=True))}
                    if is_dry_run()
                    else pmset.pmset_custom().get("b", {})
                )
                emit(
                    "dim",
                    t(
                        "impl.battery_profile_after_update",
                        v0=" ".join(f"{k} {newb[k]}" for k in pmset.POWER_KEY_ORDER if k in newb),
                    ),
                )
            else:
                emit("warn", t("impl.applying_pmset_batch_failed", v0=" ".join(chg)))
        brightness_requested = profile_brightness is not None or (
            not work
            and ask(
                t("impl.reduce_brightness_to_off_restores_the"),
                yes=yes,
                risk="aggressive",
                aggressive=aggressive,
                emit=emit,
            )
        )
        from ..system.brightness import available as brightness_available

        if (
            brightness_available() or (is_dry_run() and profile_brightness is not None)
        ) and brightness_requested:
            orig = brightness.current_brightness()
            target_brightness = str(profile_brightness if profile_brightness is not None else 0.15)
            _, rc = execute(SetBrightness(target_brightness))
            if rc == 0:
                # 只记录首次原始值: 重复降亮度时当前值已是上次降过的结果, 覆盖会让 off 恢复到暗态
                state_api.save_state(st)
                emit("ok", t("impl.brightness_was", v0=orig or "?"))
            else:
                emit("warn", t("impl.changing_brightness_failed"))
        elif profile_brightness is not None:
            emit("warn", t("impl.brightness_is_not_installed_restore_brightness", v0=profile_brightness))

        if not work:
            c.rule(t("impl.b_ac_profile_also_applies_while"))
        ac_applied = False
        ac_available = all(key in profs.get("a", {}) for key in ("powernap", "womp"))
        if not work and not ac_available:
            emit("dim", t("power.ac_unavailable"))
        if (
            not work
            and ac_available
            and ask(t("impl.reduce_ac_activity_too_disable_powernap"), default="n", yes=yes)
        ):
            _, rc = execute(SetPowerProfile(["powernap", "0", "womp", "0"], "-c"))
            ac_applied = rc == 0
            emit(
                "ok" if rc == 0 else "warn",
                t("impl.disabled_powernap_womp_on_ac") if rc == 0 else t("impl.changing_ac_settings_failed"),
            )

        c.rule(t("impl.high_energy_gui_apps"))
        scan = system_apps.scan_apps()
        for msg in scan["nobundle"]:
            emit("warn", t("impl.skipped_message", v0=msg))
        if not scan["running"]:
            emit("ok", t("impl.no_listed_high_energy_apps_are"))
        for app in scan["running"]:
            name, path = app["name"], app["path"]
            cpu = app["cpu"] or "?"
            if app["defact"] == "skip":
                emit("dim", t("impl.keeping_skip", v0=name))
                continue
            if (work or config.apps.policy == "adaptive") and app["defact"] == "ask" and yes:
                emit("dim", t("impl.default_action_for_is_ask_skipped", v0=name))
                continue
            default_act = ("skip" if work else "quit") if app["defact"] == "ask" else app["defact"]
            act = ask_qf(f"{name} (CPU {cpu}%)", default_act, yes=yes)
            spec = configured_daemon(name)
            if spec is not None and act == "quit":
                _, okq, msg = execute(QuitDaemonApp(spec, st, state_api.save_state))
                emit("ok" if okq else "warn", msg)
                continue
            if act == "quit":
                fallback = (
                    (lambda name=name: ask(t("impl.has_not_quit_possible_unsaved_dialog", v0=name), yes=yes))
                    if not work
                    else False
                )
                result = execute(QuitApp(path, name, fallback=fallback))
                if result:
                    if not getattr(result, "forced", False):
                        emit("ok", t("impl.quit", v0=name))
                    state_api.save_state(st)
                    if not yes:
                        remember.cli(name, "work" if work else "away", "quit", app["defact"], dry_run=dry_run)
                else:
                    emit("warn", t("impl.did_not_quit", v0=name))
            elif act == "freeze":
                from ..failures import probe

                assertions = probe(
                    st, name, system_apps.app_sleep_assertions, name, save=state_api.save_state, emit=emit
                )
                if assertions is None:
                    continue
                for line in assertions[:1]:
                    emit("warn", t("impl.holds_a_sleep_assertion_freezing_will", v0=name, v1=line[:90]))
                if execute(FreezeApp(path)):
                    emit(
                        "ok",
                        t(
                            "impl.frozen_processes_stopped_off_resumes_them",
                            v0=name,
                            v1=system_apps.freeze_result_count(path),
                        ),
                    )
                    state_api.save_state(st)
                    if not yes:
                        remember.cli(
                            name, "work" if work else "away", "freeze", app["defact"], dry_run=dry_run
                        )
                else:
                    emit("warn", t("impl.freezing_failed", v0=name))

        c.rule(t("impl.background_agents"))
        for s in launchd.svc_scan()["unloadable"]:
            if ask(
                t("impl.unload_agent_prompt", label=s["label"], pid=s["pid"], cpu=s["cpu"] or "?"), yes=yes
            ):
                _, rc = execute(UnloadAgent(s["plist_path"]))
                if rc == 0:
                    emit("ok", t("impl.unloaded", v0=s["label"]))
                    state_api.save_state(st)
                else:
                    emit("warn", t("impl.unloading_failed", v0=s["label"]))
        if profile.get("spotlight") != "keep" and spotlight.spotlight_on():
            if profile_spotlight_off or (
                not (work and yes)
                and ask(
                    t("impl.stop_spotlight_indexing_mdutil_i_off"),
                    default="n",
                    yes=yes if not work else False,
                    risk="aggressive",
                    aggressive=aggressive,
                    emit=emit,
                )
            ):
                _, rc = execute(StopSpotlight())
                if rc == 0:
                    emit("ok", t("impl.spotlight_indexing_disabled"))
                    state_api.save_state(st)
                else:
                    emit("warn", t("impl.mdutil_failed"))
        else:
            emit("ok", t("impl.spotlight_indexing_already_disabled"))

        if not work:
            c.rule(t("impl.remote_network_apps_kept_by_default"))
        names = ", ".join(network_api.configured_names())
        if not work and (
            profile_disconnect
            or ask(
                t("impl.disconnect_completely_quit_configured_network_apps", v0=names),
                default="n",
                yes=yes,
                risk="aggressive",
                aggressive=aggressive,
                emit=emit,
            )
        ):
            for name in network_api.configured_names():
                p = system_apps.app_path(name)
                spec = configured_daemon(name)
                if spec is not None:
                    _, okq, msg = execute(QuitDaemonApp(spec, st, state_api.save_state))
                    emit("ok" if okq else "warn", msg)
                    continue
                if not p or not system_apps.app_running(p):
                    continue
                result = execute(QuitApp(p, name, fallback=True))
                if result:
                    if not getattr(result, "forced", False):
                        emit("ok", t("impl.quit", v0=name))
                    state_api.save_state(st)
                else:
                    emit("warn", t("impl.did_not_quit", v0=name))
            dev = system_network.wifi_dev()
            if dev and (
                profile_wifi_off
                or ask(
                    t("impl.disable_wi_fi_disconnects_the_network", v0=dev),
                    yes=yes,
                    risk="aggressive",
                    aggressive=aggressive,
                    emit=emit,
                )
            ):
                if not system_network.wifi_powered(dev):
                    emit("dim", t("impl.wi_fi_already_disabled_no_action"))
                else:
                    _, rc = execute(WifiOff(dev))
                    if rc == 0:
                        emit("ok", t("impl.wi_fi_disabled"))
                        state_api.save_state(st)
                    else:
                        emit("warn", t("impl.disabling_wi_fi_failed"))
            if shutil.which("blueutil"):
                if profile_bluetooth_off or ask(
                    t("impl.disable_bluetooth_disconnects_bluetooth_audio_and"),
                    default="n",
                    yes=yes,
                    risk="aggressive",
                    aggressive=aggressive,
                    emit=emit,
                ):
                    if not system_network.bt_powered():
                        emit("dim", t("impl.bluetooth_already_disabled_no_action_needed"))
                    else:
                        _, rc = execute(BluetoothOff())
                        if rc == 0:
                            emit("ok", t("impl.bluetooth_disabled"))
                            state_api.save_state(st)
                        else:
                            emit("warn", t("impl.blueutil_failed"))
            else:
                emit("dim", t("impl.brew_install_blueutil_enables_bluetooth_control"))
            emit("warn", t("impl.configured_launchagents_must_be_unloaded_to"))

        if not work:
            c.rule(t("impl.final_assertion_audit"))
            for line in pmset.pmset_assertions():
                if re.search(r"PreventUserIdle(System|Display)Sleep.*named", line):
                    c.print(f"  {line.strip()}", highlight=False)
            sd = pmset.sleep_disabled()
            emit(
                "dim" if sd is None else "ok" if sd != "1" else "bad",
                t("power.sleep_switch_unavailable")
                if sd is None
                else t("impl.sleepdisabled_lid_close_sleep_available")
                if sd != "1"
                else t("impl.sleepdisabled_is_still_closing_the_lid"),
            )
        bl = pmset.battery_line()
        if bl:
            c.print(f"  {bl}", highlight=False)
        measurement.finish(
            before, config.measurement, st, state_api.save_state, lambda msg: emit("dim", escape(msg))
        )
        has_changes = (
            "measurement" in st
            or power_applied
            or ac_applied
            or fixsleep_applied
            or any(st[k] for k in LIST_KEYS)
            or any(st.get(k) for k in ("brightness", "wifi_off", "bt_off", "spotlight_off"))
        )
        if merged or has_changes:
            state_api.save_state(st)
            snapshot_path = common.STATE_PATH
            if is_dry_run():
                from ..system.shell import dry_run_state_dir

                snapshot_path = dry_run_state_dir() / "state.json"
            emit("ok", t("impl.snapshot_saved_at_travel_mode_off", v0=snapshot_path))
        else:
            # 幽灵锚点防护: 什么都没改就不落盘, 否则一个空锚点会成为未来会话的合并基底
            emit("dim", t("impl.no_changes_made_no_snapshot_created"))
        if not work and ask(
            t("impl.turn_off_the_display_now_pmset"),
            yes=yes,
            risk="aggressive",
            aggressive=aggressive,
            emit=emit,
        ):
            output, rc = execute(DisplaySleepNow())
            if rc:
                emit("bad", t("json.command_failed", command="pmset displaysleepnow", rc=rc, detail=output))
        from ..system.shell import dry_run_unknowns

        failed = bool(
            st.get("pending_commands")
            or (is_dry_run() and dry_run_unknowns())
            or any(not r.get("recovered", False) for r in st.get("action_failures", []))
        )
        runtime._finish_dry_run()
        return int(failed)

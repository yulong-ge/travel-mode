"""Dashboard presentation and action selection."""

from __future__ import annotations

import shutil

from rich.markup import escape
from textual.containers import Vertical
from textual.widgets import Checkbox, Label, Static

from travel_mode import network_apps as network_api
from travel_mode.actions.apps import (
    FreezeApp,
    QuitApp,
    QuitDaemonApp,
    UnloadAgent,
    configured_daemon,
)
from travel_mode.actions.network import BluetoothOff, WifiOff
from travel_mode.actions.power import (
    SetBrightness,
    SetPowerProfile,
    StopSpotlight,
)
from travel_mode.categories import pause_categories
from travel_mode.config import load_config
from travel_mode.flows import check as flow_check
from travel_mode.presentation import action_message as t
from travel_mode.system import apps as system_apps
from travel_mode.system import launchd, pmset, spotlight
from travel_mode.system import network as system_network
from travel_mode.tui.widgets import Button, _ellip, status_row


class DashboardPage:
    def compose_dashboard(self):
        yield Static(t("impl.scanning"), id="dash-battery")
        revived = Static("", id="dash-revived")
        revived.display = False
        yield revived
        reapply = Button(t("resurrection.reapply"), id="b-reapply", disabled=True)
        reapply.display = False
        yield reapply
        with Vertical(id="overview-health", classes="card") as overview:
            overview.border_title = t("health.title")
            self._dash_status = Vertical(Label(t("impl.scanning"), classes="srow"), id="dash-status")
            yield self._dash_status
            yield Vertical(Static(t("health.scanning")), id="dash-health")
        yield Button(t("health.refresh"), id="b-doctor")
        quick = Vertical(classes="card")
        quick.border_title = t("impl.save_power_before_departure")
        with quick:
            yield Checkbox(
                t("measurement.checkbox"),
                self.measure_enabled if self.measure_enabled is not None else self.scenario == "work",
                id="q-measure",
                classes="scope",
            )
            yield Static("", id="measurement-progress")
            yield Checkbox(
                t("impl.recommended_power_settings_battery_profile"),
                True,
                id="q-power",
                classes="scope",
            )
            yield Checkbox(
                t("impl.quit_freeze_apps_configured_default_actions"),
                True,
                id="q-apps",
                classes="scope",
            )
            yield Checkbox(t("impl.unload_configured_background_agents"), True, id="q-svc", classes="scope")
            yield Checkbox(
                t("categories.pause_sync"),
                self.scenario == "work" and bool({"sync", "backup"} & set(self.config.categories.pause)),
                id="q-sync",
                classes="scope",
            )
            yield Checkbox(
                t("categories.pause_updater"),
                self.scenario == "work" and "updater" in self.config.categories.pause,
                id="q-updater",
                classes="scope",
            )
            yield Checkbox(
                t("impl.stop_spotlight_indexing"),
                self.profile_values.get("spotlight") == "off",
                id="q-spot",
                classes="scope",
            )
            yield Checkbox(
                t("impl.disconnect_quit_network_apps_disable_wi"),
                self.profile_values.get("network", {}).get("disconnect", False),
                id="q-net",
                classes="scope",
            )
            yield Button(t("impl.run_selected_actions"), id="b-quick", variant="success")
        yield Button(t("measurement.energy_button"), id="b-energy")
        yield Static(t("measurement.energy_hint"), id="dash-audit")

    async def build_health(self, report):
        from textual.containers import Horizontal

        from travel_mode.flows.doctor import action_label

        box = self.query_one("#dash-health", Vertical)
        widgets = [
            Static(t("health.timing", seconds=report["elapsed_seconds"], budget=report["budget_seconds"]))
        ]
        from travel_mode.health_view import display_checks

        self.health_report = report
        for row in display_checks(report):
            if row["id"] == "sleep_disabled" and row["status"] in {"hit", "clear"}:
                continue
            if row["id"] == "keep_awake":
                import re

                shown = getattr(self, "caffeinate_targets", {})
                row["evidence"] = [
                    line
                    for line in row["evidence"]
                    if not any(re.search(rf"\bPID {pid}\b", line) for pid in shown)
                ]
                row["actions"] = [
                    d for d in row["actions"] if d["kind"] != "caffeinate" or d["process"]["pid"] not in shown
                ]
                if row["status"] == "hit" and not row["evidence"] and not row["actions"]:
                    continue
            widgets.append(Static(escape(f"{row['title']}: {t('health.' + row['status'])}")))
            if row["error"]:
                widgets.append(Static(escape(row["error"]), classes="health-detail"))
            for reference in row["references"]:
                widgets.append(Static(t("health.see", title=reference), classes="health-detail"))
            for evidence in row["evidence"]:
                widgets.append(Static(escape(evidence), classes="health-detail"))
            if row["status"] == "hit":
                for line in [row["impact"], row["manual"]]:
                    widgets.append(Static(escape(line), classes="health-detail"))
            for i, descriptor in enumerate(row["actions"]):
                button = Button(action_label(descriptor), id=f"b-health-{row['id']}-{i}")
                # Store the observation on the widget. A recomposed page cannot retarget a queued click.
                button.health_descriptor = descriptor
                widgets.append(Horizontal(Static("", classes="grow"), button, classes="health-action"))
            if (
                row["id"] == "spotlight"
                and row["status"] == "clear"
                and getattr(self, "spotlight_enabled", False)
            ):
                widgets.append(
                    Horizontal(
                        Static("", classes="grow"),
                        Button(t("impl.stop_spotlight_indexing"), id="b-spot"),
                        classes="health-action",
                    )
                )
        widgets.extend(Static(escape(line), classes="health-detail") for line in report["limitations"])
        await box.remove_children()
        await box.mount(*widgets)

    async def act_health(self, button):
        from travel_mode.flows.doctor import remediate

        if await self.busy(button, remediate, button.health_descriptor, self.execute, self.st, self.save):
            button._mutation_done = True
        self.action_refresh()

    async def build_dash(self, blocks, ac, batt, spot):
        from travel_mode.resurrection import describe

        self.spotlight_enabled = spot

        self.query_one("#dash-revived", Static).update("\n".join(describe(row) for row in self.resurrected))
        self.query_one("#dash-revived", Static).display = bool(self.resurrected)
        self.query_one("#b-reapply", Button).disabled = not self.resurrected
        self.query_one("#b-reapply", Button).display = bool(self.resurrected)
        box = self._dash_status
        await box.remove_children()
        source = t("impl.ac_power") if ac else t("impl.battery_power")
        battery = escape(batt or t("impl.battery_status_unknown"))
        w = [status_row(f"[dim]{battery} · {source}[/]", compact=True)]
        napps = len(self.scan["running"])
        nsvc = len(self.svc["unloadable"])
        self.caffeinate_targets = {target.process.pid: target for target in blocks.caffeinate_targets}
        caff_messages = {
            t("impl.caffeinate_prevents_sleep", v0=target.describe()): target
            for target in self.caffeinate_targets.values()
        }
        for kind, msg in blocks:
            if "SleepDisabled=1" in msg:
                w.append(
                    status_row(
                        "SleepDisabled=1 · " + t("health.sleep_disabled.impact"),
                        icon="[red]✘[/]",
                        button=Button(t("impl.fix"), id="b-fixsleep", classes="btn"),
                    )
                )
            elif msg in caff_messages:
                w.append(
                    status_row(
                        t("health.keep_awake.title") + ": " + escape(msg),
                        icon="[yellow]▲[/]",
                        button=Button(
                            t("impl.terminate"),
                            id=f"b-killcaff-{caff_messages[msg].process.pid}",
                            classes="btn",
                        ),
                    )
                )
            elif "daemon app" in msg and kind == "warn":
                w.append(
                    status_row(
                        escape(msg),
                        icon="[yellow]▲[/]",
                        button=Button(t("impl.quit_2"), id="b-quituu", classes="btn"),
                    )
                )
            elif t("impl.audio") in msg and kind != "ok":
                w.append(status_row(escape(msg), icon="[yellow]▲[/]", compact=True))
            elif "SleepDisabled" in msg and kind == "ok":
                w.append(status_row(escape(msg), icon="[green]✔[/]", compact=True))
        apps = (
            t("impl.high_energy_apps_running", v0=napps, v1="" if napps else t("impl.none"))
            if "p-apps" in self.pages_ready
            else t("impl.scanning")
        )
        w.append(status_row(apps, button=Button(t("impl.review"), id="b-dash-apps", classes="btn")))
        services = (
            t("impl.background_agents_unloadable", v0=nsvc, v1="" if nsvc else t("impl.none"))
            if "p-svc" in self.pages_ready
            else t("impl.scanning")
        )
        w.append(status_row(services, button=Button(t("impl.review"), id="b-dash-svc", classes="btn")))
        await box.mount(*w)
        if hasattr(self, "health_report"):
            await self.build_health(self.health_report)

    async def act_quick(self, btn):
        measure_requested = self.query_one("#q-measure", Checkbox).value
        scope = {
            k: self.query_one(f"#q-{k}", Checkbox).value
            for k in ("power", "apps", "svc", "spot", "net", "sync", "updater")
        }
        if not any(scope.values()):
            self.notify(t("impl.no_action_groups_selected"), severity="warning")
            return

        def flow():
            config = load_config()
            from travel_mode import measurement

            def progress(message):
                self.tlog("dim", message)
                self.call_from_thread(self.update_measurement_progress, message)

            clock = measurement.CancellableClock(self._measurement_cancelled)
            before = measurement.begin(
                self.scenario,
                measure_requested,
                config.measurement,
                progress,
                clock=clock,
                cancelled=self._measurement_cancelled,
            )
            categories = set()
            if scope["sync"]:
                categories.update({"sync", "backup"} & set(config.categories.pause))
            if scope["updater"] and "updater" in config.categories.pause:
                categories.add("updater")
            if categories:
                service_scan = launchd.svc_scan()
                pause_categories(
                    categories,
                    system_apps.scan_apps()["running"],
                    service_scan.get("recognized", service_scan["unloadable"]),
                    self.execute,
                    self.save,
                    self.st,
                )
            if scope["power"]:
                self.tlog("dim", t("impl.applying_recommended_power_settings"))
                chg = []
                recommendations = pmset.power_recommendations()
                for k, _, rec in recommendations:
                    chg += [k, rec]
                if chg:
                    _, rc = self.execute(SetPowerProfile([*chg]))
                else:
                    rc = None
                self.tlog(
                    "ok" if rc == 0 else "dim" if rc is None else "warn",
                    t("impl.battery_profile_updated_settings", v0=len(recommendations))
                    if rc == 0
                    else t("power.battery_unavailable")
                    if rc is None
                    else t("impl.applying_pmset_failed"),
                )
                if rc == 0:
                    self.save()  # 电源变更落盘 (否则 off 无法回滚且污染下次锚点)
                target = self.profile_values.get("brightness")
                if target is not None:
                    _, rc = self.execute(SetBrightness(target))
                    if rc:
                        self.tlog("bad", t("impl.changing_brightness_failed"))
                    else:
                        self.save()
            if scope["apps"]:
                nq = nf = 0
                ask_pending = []
                for app in system_apps.scan_apps()["running"]:
                    name, path, dfl = app["name"], app["path"], app["defact"]
                    if path in (*self.st.get("quit_apps", []), *self.st.get("frozen_apps", [])):
                        continue
                    if (
                        app.get("category") in {"sync", "backup", "updater"}
                        and name not in config.apps.configured_actions
                    ):
                        continue
                    if dfl == "ask":
                        # conf 的 ask = "问我" — 一键流程无人应答, 跳过并列出, 绝不强杀
                        ask_pending.append(name)
                        self.tlog("dim", t("impl.default_action_for_is_ask_skipped", v0=name))
                        continue
                    if dfl == "quit":
                        spec = configured_daemon(name)
                        if spec is not None:
                            _, okq, msg = self.execute(QuitDaemonApp(spec, self.st, self.save))
                            if msg:
                                self.tlog("ok" if okq else "warn", msg)
                            nq += int(okq)
                            continue
                        result = self.execute(QuitApp(path, name, fallback=self.scenario != "work"))
                        if result:
                            self.save()
                            nq += 1
                            if not getattr(result, "forced", False):
                                self.tlog("ok", t("impl.quit", v0=name))
                        else:
                            self.tlog("warn", t("impl.did_not_quit", v0=name))
                    elif dfl == "freeze":
                        from travel_mode.failures import probe

                        assertions = probe(
                            self.st,
                            name,
                            system_apps.app_sleep_assertions,
                            name,
                            save=self.save,
                            emit=self.tlog,
                        )
                        if assertions is None:
                            continue
                        for _line in assertions[:1]:
                            self.tlog("warn", t("impl.holds_a_sleep_assertion_freezing_will_2", v0=name))
                        if self.execute(FreezeApp(path)):
                            self.save()
                            nf += 1
                            self.tlog("ok", t("impl.frozen_2", v0=name))
                        else:
                            self.tlog("warn", t("impl.freezing_failed", v0=name))
                msg = t(
                    "impl.apps_done_quit_frozen",
                    v0=nq,
                    v1=nf,
                    v2=t("impl.ask_requires_manual_action", v0=", ".join(ask_pending)) if ask_pending else "",
                )
                self.tlog("ok", msg)
            if scope["svc"]:
                n = 0
                for s in launchd.svc_scan()["unloadable"]:
                    if s["plist_path"] in self.st.get("unloaded_svcs", []):
                        continue
                    _, rc = self.execute(UnloadAgent(s["plist_path"]))
                    if rc == 0:
                        self.save()
                        n += 1
                        self.tlog("ok", t("impl.unloaded", v0=s["label"]))
                    else:
                        self.tlog("warn", t("impl.unloading_failed_2", v0=s["label"]))
                self.tlog("dim", t("impl.agents_unloaded", v0=n))
            if scope["spot"]:
                if not spotlight.spotlight_on():
                    self.tlog("dim", t("impl.spotlight_indexing_already_disabled_no_action"))
                else:
                    _, rc = self.execute(StopSpotlight())
                    if rc == 0:
                        self.save()
                        self.tlog("ok", t("impl.spotlight_indexing_disabled"))
                    else:
                        self.tlog("warn", t("impl.mdutil_failed"))
            if scope["net"]:
                for name in network_api.configured_names():
                    spec = configured_daemon(name)
                    if spec is not None:
                        # root daemon 会被 launchd 拉活且持防睡断言, grace_quit 是假成功
                        p_uu = system_apps.app_path(name)
                        if (p_uu and system_apps.app_running(p_uu)) or pmset.sleep_blocking_assertion():
                            _, okq, msg = self.execute(QuitDaemonApp(spec, self.st, self.save))
                            if msg:
                                self.tlog("ok" if okq else "warn", f"{name}: {msg}")
                        continue
                    p = system_apps.app_path(name)
                    if p and system_apps.app_running(p):
                        result = self.execute(QuitApp(p, name, fallback=self.scenario != "work"))
                        if result:
                            self.save()
                            if not getattr(result, "forced", False):
                                self.tlog("ok", t("impl.quit", v0=name))
                        else:
                            self.tlog("warn", t("impl.did_not_quit", v0=name))
                dev = system_network.wifi_dev()
                if dev:
                    if not system_network.wifi_powered(dev):
                        self.tlog("dim", t("impl.wi_fi_already_disabled_no_action"))
                    else:
                        _, rc = self.execute(WifiOff(dev))
                        if rc == 0:
                            self.save()
                            self.tlog("ok", t("impl.wi_fi_disabled_2", v0=dev))
                if shutil.which("blueutil"):
                    if not system_network.bt_powered():
                        self.tlog("dim", t("impl.bluetooth_already_disabled_no_action_needed"))
                    else:
                        _, rc = self.execute(BluetoothOff())
                        if rc == 0:
                            self.save()
                            self.tlog("ok", t("impl.bluetooth_disabled"))
            sd = pmset.sleep_disabled()
            self.tlog(
                "dim" if sd is None else "bad" if sd == "1" else "ok",
                t("power.sleep_switch_unavailable")
                if sd is None
                else t(
                    "impl.final_audit_sleepdisabled",
                    v0=sd,
                    v1="" if sd != "1" else t("impl.still_prevents_sleep"),
                ),
            )
            for kind, msg in flow_check.blockers_lines():
                if t("impl.audio") in msg or "daemon app" in msg:
                    self.tlog(kind, t("impl.final_audit", v0=_ellip(msg, 70)))
            self.tlog("ok", t("impl.power_saving_actions_complete_snapshot_updated"))
            measurement.finish(
                before,
                config.measurement,
                self.st,
                self.save,
                progress,
                clock=clock,
                cancelled=self._measurement_cancelled,
            )

        await self.busy(btn, flow)
        self.notify(t("impl.power_saving_actions_complete"), severity="information")
        self.action_refresh()

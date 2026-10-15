"""Travel-mode Textual application and refresh lifecycle."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import ClassVar

from rich.markup import escape
from textual import work
from textual.app import App
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import RichLog, Static, TabbedContent

from travel_mode import snapshots
from travel_mode import state as state_api
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.config import load_config
from travel_mode.flows import check as flow_check
from travel_mode.flows import common
from travel_mode.presentation import action_message as t
from travel_mode.profiles import active_name, active_scenario, active_values, profile_scope
from travel_mode.system import apps as system_apps
from travel_mode.system import launchd, pmset, spotlight
from travel_mode.system import network as system_network
from travel_mode.system.shell import is_dry_run

from .dispatch import Dispatch
from .layout import Layout
from .pages.apps import AppsPage
from .pages.dashboard import DashboardPage
from .pages.network import NetworkPage
from .pages.power import PowerPage
from .pages.services import ServicesPage


class TravelApp(Layout, Dispatch, DashboardPage, PowerPage, AppsPage, ServicesPage, NetworkPage, App):
    TITLE = "travel-mode"

    SUB_TITLE = t("impl.travel_mode_dry_run") if is_dry_run() else t("impl.travel_mode")

    AUTO_FOCUS = "#body"

    BINDINGS: ClassVar = [
        Binding("w", "select_scenario('work')", "", show=False),
        Binding("a", "select_scenario('away')", "", show=False),
        Binding("q", "quit", t("impl.quit_2")),
        Binding("l", "toggle_language", t("language.current")),
        Binding("r", "refresh", t("impl.refresh")),
        Binding("1", "goto('p-dash')", t("impl.overview"), show=False),
        Binding("2", "goto('p-power')", t("impl.power"), show=False),
        Binding("3", "goto('p-apps')", "Apps", show=False),
        Binding("4", "goto('p-svc')", t("services.title"), show=False),
        Binding("5", "goto('p-net')", t("impl.network"), show=False),
    ]

    CSS_PATH = str(Path(__file__).with_name("style.tcss"))

    def __init__(self, dry_run=False, *, scenario=None, measure=None):
        super().__init__()
        self._dry_run = dry_run
        self.measure_enabled = measure
        import threading

        self._measurement_cancelled = threading.Event()
        self._battery_timer = None
        self.resurrected = []
        self.profile_values = active_values()
        self.scenario = scenario or active_scenario()
        self.config = load_config()
        self.profile_name = active_name() or None
        if self.scenario != "ask":
            with self.scenario_scope():
                self.profile_values = active_values()
        self.sub_title = (
            t("impl.travel_mode_dry_run") if self._dry_run or is_dry_run() else t("impl.travel_mode")
        )
        self.st, self.merged = state_api.new_state(), False
        self.origin_ready = False
        self.scan = {"running": [], "nobundle": []}
        self.svc = {"unloadable": [], "kept": []}
        self.pw_boxes = []  # (key, rec, checkbox)
        self.app_rows = {}  # idx -> dict(name,path,btns,label,done,kill)
        self.svc_btns = {}
        self.net_paths = {}
        self.refresh_complete = False
        self._refresh_lock = asyncio.Lock()
        self._refresh_generation = 0
        self.pages_ready = set()
        self.executor = Executor()
        self._mutation_lock = asyncio.Lock()
        self._mutation_generation = 0
        self._mutation_action = None
        self._disabled_buttons = {}
        self._transaction_view = None

    def execute(self, action):
        return self.executor.execute(action, Context(self.st, self.save))

    def _disable_mutation_button(self, button):
        if self._mutation_action is not None and button.id not in {"b-dash-apps", "b-dash-svc"}:
            self._disabled_buttons.setdefault(button, button.disabled)
            button.disabled = True

    def display_state(self):
        from travel_mode.state import state_snapshot

        # During a transaction, the UI only traverses its admission snapshot.
        return state_snapshot(self._transaction_view if self._transaction_view is not None else self.st)

    def logline(self, kind, msg):
        mark = {"ok": "[green]✔[/]", "warn": "[yellow]▲[/]", "bad": "[red]✘[/]", "dim": "[dim]○[/]"}[kind]
        self.query_one("#log", RichLog).write(f"{mark} {escape(msg)}")

    def tlog(self, kind, msg):
        self.call_from_thread(self.logline, kind, msg)

    def save(self, state=None):
        # 防复活: CLI 的 off 已删除快照后, 本进程的内存副本不得整份写回
        from travel_mode.state import _path

        if self.merged and not _path().exists():
            import threading

            msg = t("impl.snapshot_deleted_externally_possibly_by_off")
            if threading.current_thread() is threading.main_thread():
                self.logline("bad", msg)
                self.notify(t("impl.snapshot_cleared_by_off_restart_travel"), severity="error")
            else:
                self.call_from_thread(self.logline, "bad", msg)
            from travel_mode.state import StateError

            raise StateError(msg)
        state_api.save_state(self.st if state is None else state)
        self.merged = True

    async def action_toggle_language(self):
        from travel_mode.config import write_language
        from travel_mode.i18n import get_language, set_language

        language = "zh" if get_language() == "en" else "en"
        try:
            write_language(language)
        except (OSError, ValueError, TypeError) as exc:
            self.logline("bad", t("language.write_failed", error=exc))
            self.notify(t("language.write_failed", error=exc), severity="error")
            return
        self.refresh_complete = False
        self._refresh_generation += 1
        self.workers.cancel_group(self, "dash")
        self.workers.cancel_group(self, "audit")
        # 重组与回填共用锁，旧回填不能挂载到已经移除的页面。
        async with self._refresh_lock:
            self.refresh_complete = False
            set_language(language)
            await self.recompose()
            self.sub_title = (
                t("impl.executing_action", action=self._mutation_action)
                if self._mutation_action is not None
                else t("impl.travel_mode_dry_run")
                if self._dry_run or is_dry_run()
                else t("impl.travel_mode")
            )
            self._translate_bindings()
            self.refresh_bindings()
            if self.scenario != "ask":
                self.action_refresh()

    def _translate_bindings(self):
        from dataclasses import replace

        for key, message in (("q", "impl.quit_2"), ("l", "language.current"), ("r", "impl.refresh")):
            self._bindings.key_to_bindings[key] = [
                replace(binding, description=t(message)) for binding in self._bindings.key_to_bindings[key]
            ]

    async def busy(self, btn, fn, *a):
        btn.loading = True
        try:
            return await asyncio.to_thread(fn, *a)
        except Exception as e:
            self.logline("bad", t("impl.operation_failed", v0=type(e).__name__, v1=e))
            self.notify(t("impl.operation_failed_2", v0=e), severity="error")
            raise RuntimeError(t("impl.tui_worker_operation_failed", v0=e)) from e
        finally:
            # Recompose disposes the old button; only mounted widgets receive UI updates.
            if btn.is_attached:
                btn.loading = False

    @work(exclusive=True, group="dash")
    async def refresh_dash(self):
        with self.scenario_scope():
            from textual.worker import get_current_worker

            from travel_mode.network_apps import network_scan
            from travel_mode.system.app_snapshot import AppSnapshot, snapshot_scope
            from travel_mode.system.platform import supported_pmset_keys

            worker = get_current_worker()
            generation = self._refresh_generation
            dash_values = None
            self.pages_ready = set()
            self.refresh_complete = False

            async def apply(page, build, *args):
                async with self._refresh_lock:
                    if worker.is_cancelled or generation != self._refresh_generation:
                        return
                    # 每页原子挂载；其它页无需等待慢扫描结束。
                    with self.batch_update():
                        await build(*args)
                        if not worker.is_cancelled and generation == self._refresh_generation:
                            self.pages_ready.add(page)
                            if page in {"p-apps", "p-svc"} and "p-dash" in self.pages_ready:
                                await self.build_dash(*dash_values)
                    from travel_mode.tui.messages import PageRefreshed

                    self.post_message(PageRefreshed(page))

            async def dash():
                nonlocal dash_values
                blocks, ac, batt, spot = await asyncio.to_thread(
                    lambda: (
                        flow_check.blockers_lines(),
                        pmset.on_ac_power(),
                        pmset.battery_line(),
                        spotlight.spotlight_on(),
                    )
                )
                dash_values = blocks, ac, batt, spot
                from travel_mode.resurrection import detect

                self.resurrected = await asyncio.to_thread(detect, self.display_state())
                await apply("p-dash", self.build_dash, *dash_values)

            async def power():
                profs, supported = await asyncio.to_thread(
                    lambda: (pmset.pmset_custom(), supported_pmset_keys())
                )
                await apply("p-power", self.build_power, profs, supported)

            async def services():
                svc, spot = await asyncio.to_thread(lambda: (launchd.svc_scan(), spotlight.spotlight_on()))

                async def build():
                    self.svc = svc
                    await self.build_svc(spot)

                await apply("p-svc", build)

            # 应用和网络页只共享本轮索引/进程快照，不共享完成时序。
            snapshot_task = asyncio.create_task(
                asyncio.to_thread(AppSnapshot.capture, system_apps.app_directories())
            )

            async def sample_apps(fn):
                snapshot = await asyncio.shield(snapshot_task)

                def sample():
                    with snapshot_scope(snapshot):
                        return fn()

                return await asyncio.to_thread(sample)

            async def apps():
                scan = await sample_apps(system_apps.scan_apps)

                async def build():
                    self.scan = scan
                    await self.build_apps()

                await apply("p-apps", build)

            async def network():
                rows = await sample_apps(network_scan)

                def connections():
                    dev = system_network.wifi_dev()
                    return (
                        dev,
                        system_network.wifi_powered(dev) if dev else None,
                        system_network.bt_powered() if shutil.which("blueutil") else None,
                    )

                dev, wifi, bluetooth = await asyncio.to_thread(connections)
                await apply("p-net", self.build_net, rows, dev, wifi, bluetooth)

            tasks = [asyncio.create_task(fn()) for fn in (dash, power, services, apps, network)]
            try:
                await asyncio.gather(*tasks)
                if not worker.is_cancelled and generation == self._refresh_generation:
                    self.refresh_complete = True
            except Exception as e:
                self.logline("bad", t("impl.refresh_failed", v0=type(e).__name__, v1=e))
                self.notify(t("impl.refresh_failed_2", v0=e), severity="error")
                raise RuntimeError(t("impl.tui_refresh_failed", v0=e)) from e
            finally:
                # 取消所有待回填页；线程返回后也不能污染语言重组后的页面。
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                snapshot_task.cancel()
                await asyncio.gather(snapshot_task, return_exceptions=True)

    @work(thread=True, exclusive=True, group="audit")
    def refresh_audit_detail(self):
        with self.scenario_scope():
            from textual.worker import get_current_worker

            worker = get_current_worker()

            async def apply():
                async with self._refresh_lock:
                    if not worker.is_cancelled:
                        self.query_one("#dash-audit", Static).update("\n".join(lines))

            try:
                from travel_mode.system.processes import power_top

                lines = [
                    t("impl.top_energy_consumers"),
                    *(f"{power}  {command}" for power, command in power_top()),
                ]
                if not worker.is_cancelled:
                    self.call_from_thread(apply)
            except Exception as e:
                self.call_from_thread(self.logline, "bad", t("impl.audit_failed", v0=type(e).__name__, v1=e))
                self.call_from_thread(self.notify, t("impl.audit_failed_2", v0=e), severity="error")
                raise RuntimeError(t("impl.tui_audit_failed", v0=e)) from e

    @work(exclusive=True, group="battery")
    async def refresh_battery(self):
        from textual.worker import get_current_worker

        from travel_mode.system import battery

        reading = await asyncio.to_thread(battery.read)
        if not get_current_worker().is_cancelled and self.is_running:
            self.query_one("#dash-battery", Static).update(battery.describe(reading))

    def on_unmount(self):
        self._measurement_cancelled.set()
        if self._battery_timer is not None:
            self._battery_timer.stop()

    def on_mount(self):
        self._translate_bindings()
        self.refresh_bindings()
        if self.scenario == "ask":
            self.query_one("#scenario-work").focus()
            return
        self.initialize_origin()

    def scenario_scope(self):
        return profile_scope(self.config, self.profile_name, scenario=self.scenario)

    @work(exclusive=True, group="origin")
    async def initialize_origin(self):
        with self.scenario_scope() as config:
            self.st, self.merged = await asyncio.to_thread(snapshots.snapshot_origin)
            self.st["scenario"] = config.options.scenario
            self.origin_ready = True
            self.start_pages()

    async def choose_scenario(self, scenario):
        self.scenario = scenario
        with self.scenario_scope():
            self.profile_values = active_values()
        await self.recompose()
        self.initialize_origin()

    async def action_select_scenario(self, scenario):
        if self.scenario == "ask":
            await self.choose_scenario(scenario)

    def start_pages(self):
        if self.merged:
            age = snapshots.anchor_age_hours(self.st)
            age_s = t("impl.hours_ago", v0=age) if age is not None else ""
            self.logline(
                "warn",
                t(
                    "impl.existing_snapshot_created_original_values_preserved",
                    v0=self.st.get("anchored_at") or self.st.get("saved_at", "?"),
                    v1=age_s,
                ),
            )
            if age is not None and age > 24:
                self.logline("warn", t("impl.old_snapshot_off_rolls_back_to_2"))
        if system_apps.ensure_conf():
            self.logline("dim", t("impl.created_default_action_configuration", v0=common.CONF_PATH))
        self.refresh_dash()
        self.refresh_battery()
        if self._battery_timer is None:
            self._battery_timer = self.set_interval(30, self.refresh_battery)

    def action_refresh(self):
        if self.scenario == "ask" or not self.origin_ready:
            return
        self._refresh_generation += 1
        self.refresh_complete = False
        self.logline("dim", t("impl.refresh"))
        self.refresh_dash()
        self.refresh_battery()

    def action_goto(self, pane: str):
        if self.scenario == "ask":
            return
        self.query_one(TabbedContent).active = pane

    def on_tabbed_content_tab_activated(self, event):
        """1) 同步段: 若焦点仍滞留在旧 pane, 立即交给 Tabs 本体 — 否则隐藏 pane 重新获得焦点会
        触发 TabPane.Focused 把 active 弹回旧页 (新页 display 永不生效);
        2) 异步段: 等新 pane 真正显示后把焦点交给它的滚动容器 (方向键可滚页)."""
        tb = self.query_one(TabbedContent)
        new_pane = self.query_one(f"#{tb.active}") if tb.active else None
        f = self.focused
        if (
            f is not None
            and f is not tb
            and (new_pane is None or (f is not new_pane and new_pane not in f.ancestors))
        ):
            tb.focus()

        async def fix():
            pane = None
            for _ in range(40):
                pane = self.query_one(f"#{tb.active}")
                if pane.display and pane.size.height > 0:
                    break
                await asyncio.sleep(0.05)
            if pane is None or not pane.display:
                return
            pane.refresh(layout=True)
            f = self.focused
            if f is None or (f is not tb and f is not pane and pane not in f.ancestors):
                pane.query_one(VerticalScroll).focus()

        self.run_worker(fix, group="tabfix", exclusive=True)


def tui(return_class=False, dry_run=False, measure=None):
    if return_class:
        if not dry_run:
            return TravelApp

        class DryRunTravelApp(TravelApp):
            def __init__(self):
                super().__init__(dry_run=True)

        return DryRunTravelApp
    return TravelApp(dry_run=dry_run, measure=measure).run()


def run_tui():
    return tui()

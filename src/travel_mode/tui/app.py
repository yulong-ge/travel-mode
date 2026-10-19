"""Travel-mode Textual application and refresh lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from rich.markup import escape
from textual import work
from textual.app import App
from textual.binding import Binding
from textual.compose import compose
from textual.widgets import RichLog, Static

from travel_mode import snapshots
from travel_mode import state as state_api
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.config import load_config
from travel_mode.flows import common
from travel_mode.presentation import action_message as t
from travel_mode.profiles import active_name, active_scenario, active_values, profile_scope
from travel_mode.system import apps as system_apps
from travel_mode.system.shell import is_dry_run

from .capabilities import Capabilities
from .dispatch import Dispatch
from .lifecycle import Lifecycle
from .workflow import Workflow


class TravelApp(Workflow, Lifecycle, Dispatch, Capabilities, App):
    TITLE = "travel-mode"

    SUB_TITLE = t("impl.travel_mode_dry_run") if is_dry_run() else t("impl.travel_mode")

    AUTO_FOCUS = "#body"

    BINDINGS: ClassVar = [
        Binding("w", "select_scenario('work')", "", show=False),
        Binding("a", "select_scenario('away')", "", show=False),
        Binding("q", "quit", t("impl.quit_2")),
        Binding("l", "toggle_language", t("redesign.language")),
        Binding("r", "refresh", t("impl.refresh")),
        Binding("d", "details", t("redesign.details")),
        Binding("question_mark", "help", t("redesign.help")),
        Binding("1", "goto('p-dash')", t("impl.overview"), show=False),
        Binding("2", "goto('p-power')", t("impl.power"), show=False),
        Binding("3", "goto('p-apps')", "Apps", show=False),
        Binding("4", "goto('p-svc')", t("services.title"), show=False),
        Binding("5", "goto('p-net')", t("impl.network"), show=False),
    ]

    CSS_PATH = str(Path(__file__).with_name("style.tcss"))

    def __init__(self, dry_run=False, *, scenario=None, measure=None, first_run=False):
        super().__init__()
        self._init_lifecycle()
        self._recomposing = False
        self._pending_logs = []
        self._pending_progress = None
        self._dry_run = dry_run
        self._setup_first_run = first_run
        from travel_mode.flows.setup import Remember

        self.remember = Remember()
        self.measure_enabled = measure
        import threading

        self._measurement_cancelled = threading.Event()
        self._battery_timer = None
        self.resurrected = []
        self._observed_resurrections = []
        self._observed_external = False
        self.profile_values = active_values()
        self.scenario = scenario or active_scenario()
        self.config = load_config()
        self.profile_name = active_name() or None
        if self.scenario == "ask":
            existing = state_api.load_state()
            if existing and existing.get("scenario") in {"work", "away"}:
                self.scenario = existing["scenario"]
                self._setup_first_run = False
        if self.scenario != "ask":
            with self.scenario_scope():
                self.profile_values = active_values()
        self.sub_title = (
            t("impl.travel_mode_dry_run") if self._dry_run or is_dry_run() else t("impl.travel_mode")
        )
        self.st, self.merged = state_api.new_state(), False
        self._session_admitted = False
        self.session_decision_complete = asyncio.Event()
        self.origin_ready = False
        self._origin_error = None
        self.scan = {"running": [], "nobundle": []}
        self.svc = {"unloadable": [], "kept": []}
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
        self.init_workflow()

    def execute(self, action):
        from travel_mode.failures import reporter

        with reporter(self.action_notice):
            return self.executor.execute(action, Context(self.st, self.save))

    def run_worker(self, *args, **kwargs):
        import os

        if os.environ.get("TRAVEL_MODE_DEBUG") != "1":
            kwargs["exit_on_error"] = False
        return super().run_worker(*args, **kwargs)

    def on_worker_state_changed(self, event):
        from textual.worker import WorkerState

        if event.state == WorkerState.ERROR:
            self.report_action_error(event.worker.error, t("failure.worker", group=event.worker.group))
            self.worker_error_reported(event.worker).set()

    def action_notice(self, kind, message):
        import threading

        if threading.current_thread() is threading.main_thread():
            self.logline(kind, message)
        else:
            self.tlog(kind, message)

    def report_action_error(self, error, action=None):
        from travel_mode import failures
        from travel_mode.state import StateError
        from travel_mode.system.shell import ReadError, SudoReadError

        expected = isinstance(error, (failures.CommandError, ReadError, SudoReadError, PermissionError))

        row = getattr(error, "travel_failure", None)
        if row is None:
            if (
                "session" not in self.st
                and not isinstance(error, StateError)
                and self._mutation_action is not None
            ):
                from travel_mode import sessions

                sessions.begin(self.st)
            if "session" in self.st:
                counts = self.st["session"]["actions"]
                counts["attempted"] += 1
                counts["failed"] += 1
            try:
                row = failures.record(
                    self.st,
                    action or self._mutation_action or t("failure.operation"),
                    str(error),
                    error=error,
                    kind="state" if isinstance(error, StateError) else "action" if expected else "internal",
                    save=self.save if "session" in self.st else None,
                )
            except StateError as log_error:
                message = t(
                    "failure.logging_failed", reason=error, logging=log_error, path=failures.log_path()
                )
                self.logline("bad", message)
                self.notify(message, severity="error")
                return
        message = (
            failures.message(row)
            if expected
            else t(
                "failure.stopped" if isinstance(error, StateError) else "failure.internal",
                kind=type(error).__name__,
                reason=" ".join(str(error).split()),
                path=failures.log_path(),
            )
        )
        self.logline("bad", message)
        self.notify(message, severity="error")

    def _disable_mutation_button(self, button):
        mutates = button.id in {"b-quick", "b-off", "b-reapply"} or (button.id or "").startswith(
            ("ready-", "b-health-", "b-killcaff-", "retry-", "force-", "thaw-")
        )
        if not mutates:
            return
        if self._origin_error is not None:
            button.disabled = True
            return
        if self._mutation_action is not None and button.id not in {"b-dash-apps", "b-dash-svc"}:
            self._disabled_buttons.setdefault(button, button.disabled)
            button.disabled = True

    def main_query_one(self, selector, expect_type=None):
        # Read workers update the mounted main screen even while a modal is open.
        return self.screen_stack[0].query_one(selector, expect_type)

    def main_query(self, selector=None):
        return self.screen_stack[0].query(selector)

    def display_state(self):
        from travel_mode.state import state_snapshot

        # During a transaction, the UI only traverses its admission snapshot.
        return state_snapshot(self._transaction_view if self._transaction_view is not None else self.st)

    def logline(self, kind, msg):
        if kind != "dim":
            self.last_result = msg
        if self._recomposing or self.scenario == "ask":
            self._pending_logs.append((kind, msg))
            return
        mark = {"ok": "[green]✔[/]", "warn": "[yellow]▲[/]", "bad": "[red]✘[/]", "dim": "[dim]○[/]"}[kind]
        records = getattr(self, "_drawer_logs", [])
        records.append((mark, msg))
        self._drawer_logs = records[-400:]
        if kind != "dim":
            self.main_query_one("#last-result", Static).update(f"{mark} {escape(msg)}")
        self.refresh_detail_log()

    def refresh_detail_log(self):
        if self._recomposing or self.scenario == "ask" or self._interface_closing:
            return
        from .widgets import ActionFooter

        footer = self.main_query_one("#last-result", ActionFooter)
        log = self.main_query_one("#log", RichLog)
        log.clear()
        for icon, text in getattr(self, "_drawer_logs", []):
            # A short result is already fully visible. An abbreviated result
            # remains available here in full, in the scrollable drawer log.
            if text != self.last_result or footer.truncated:
                log.write(f"{icon} {escape(text)}")

    def update_measurement_progress(self, message):
        self._pending_progress = message
        if not self._recomposing:
            self.main_query_one("#measurement-progress", Static).update(escape(message))

    async def recompose(self):
        self._recomposing = True
        try:
            # A restore can open a report before a queued language switch runs.
            # Rebuild the product screen, preserving the report's own widget tree.
            screen = self.default_screen
            async with screen.batch():
                await screen.query("*").exclude(".-textual-system").remove()
                await screen.mount_all(compose(self))
        finally:
            self._recomposing = False
        logs, self._pending_logs = self._pending_logs, []
        for kind, message in logs:
            self.logline(kind, message)
        if self._pending_progress is not None:
            self.update_measurement_progress(self._pending_progress)
        if self.scenario != "ask":
            self.main_query_one("#body").focus()

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
        completion = self.track_input()
        self.last_language_dispatch = completion
        self.language_posted.set()
        try:
            async with self._mutation_lock:
                if not self._interface_closing:
                    await self._toggle_language()
        finally:
            self.complete_input(completion)

    async def _toggle_language(self):
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
        self.workers.cancel_group(self, "battery")
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
            else:
                self.main_query_one("#scenario-work").focus()

    def _translate_bindings(self):
        from dataclasses import replace

        for key, message in (
            ("q", "impl.quit_2"),
            ("l", "redesign.language"),
            ("r", "impl.refresh"),
            ("d", "redesign.details"),
            ("question_mark", "redesign.help"),
        ):
            self._bindings.key_to_bindings[key] = [
                replace(binding, description=t(message)) for binding in self._bindings.key_to_bindings[key]
            ]

    async def busy(self, btn, fn, *a):
        btn.loading = True
        try:
            return await self.in_thread(fn, *a)
        except Exception as e:
            import os

            self.report_action_error(e)
            if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
                raise
            from travel_mode.failures import BatchStopped

            raise BatchStopped() from e
        finally:
            # Recompose disposes the old button; only mounted widgets receive UI updates.
            if btn.is_attached:
                btn.loading = False

    @work(thread=True, exclusive=True, group="audit")
    def refresh_audit_detail(self):
        with self.scenario_scope():
            from textual.worker import get_current_worker

            worker = get_current_worker()
            generation = self._refresh_generation

            async def apply():
                async with self._refresh_lock:
                    if (
                        not worker.is_cancelled
                        and generation == self._refresh_generation
                        and not self._interface_closing
                    ):
                        self.main_query_one("#dash-audit", Static).update("\n".join(lines))

            try:
                from travel_mode.system.processes import power_top

                lines = [
                    t("impl.top_energy_consumers"),
                    *(f"{power}  {command}" for power, command in power_top()),
                ]
                if not worker.is_cancelled:
                    self.call_from_thread(apply)
            except Exception as e:
                if worker.is_cancelled or generation != self._refresh_generation or self._interface_closing:
                    return

                def report_error(error):
                    if (
                        worker.is_cancelled
                        or generation != self._refresh_generation
                        or self._interface_closing
                    ):
                        return
                    self.logline("bad", t("impl.audit_failed", v0=type(error).__name__, v1=error))
                    self.notify(t("impl.audit_failed_2", v0=error), severity="error")

                self.call_from_thread(report_error, e)
                raise RuntimeError(t("impl.tui_audit_failed", v0=e)) from e

    @work(exclusive=True, group="battery")
    async def refresh_battery(self):
        from textual.worker import get_current_worker

        from travel_mode.system import battery
        from travel_mode.system.shell import ReadError

        generation = self._refresh_generation
        try:
            reading = await asyncio.to_thread(battery.read)
        except (ReadError, battery.BatteryError) as exc:
            if (
                not get_current_worker().is_cancelled
                and generation == self._refresh_generation
                and not self._interface_closing
            ):
                message = t("measurement.failed", reason=str(exc))
                self.main_query_one("#dash-battery", Static).update(escape(message))
                self.logline("warn", message)
            return
        if (
            not get_current_worker().is_cancelled
            and generation == self._refresh_generation
            and not self._interface_closing
        ):
            self._observed_external |= reading.get("reason") in {"external_power", "charging"}
            if not self._mutation_lock.locked():
                self.flush_session_observations()
            self.current_battery = reading
            self.main_query_one("#dash-battery", Static).update(self.battery_heading(reading))
            self.update_session()

    def flush_session_observations(self):
        from travel_mode.sessions import note

        note(self.st, self._observed_resurrections, external=self._observed_external, save=self.save)
        self._observed_resurrections.clear()
        self._observed_external = False

    def on_unmount(self):
        self._measurement_cancelled.set()
        if self._battery_timer is not None:
            self._battery_timer.stop()

    def on_mount(self):
        self._translate_bindings()
        self.refresh_bindings()
        if self.scenario == "ask":
            self.main_query_one("#scenario-work").focus()
            return
        if self._setup_first_run:
            self.open_setup(initial=True)
        else:
            self.initialize_origin()

    def open_setup(self, *, initial=False):
        from .setup import SetupScreen

        def finished(text):
            from travel_mode.paths import config_path

            self.config = load_config(config_path())
            self._setup_first_run = False
            if initial:
                self.initialize_origin()
            else:
                self.action_refresh()
            if text:
                self.logline("ok", text)

        self.push_screen(SetupScreen(dry_run=self._dry_run), finished)

    def scenario_scope(self):
        return profile_scope(self.config, self.profile_name, scenario=self.scenario)

    @work(exclusive=True, group="origin")
    async def initialize_origin(self):
        with self.scenario_scope() as config:
            from travel_mode.system.shell import ReadError

            try:
                origin, merged = await asyncio.to_thread(snapshots.snapshot_origin)
            except ReadError as exc:
                self._origin_error = str(exc)
                from .widgets import Button

                for button in self.main_query(Button):
                    self._disable_mutation_button(button)
                self.logline("warn", t("health.origin_failed", reason=str(exc)))
                self.refresh_dash()
                self.refresh_battery()
                return
            from textual.worker import get_current_worker

            if get_current_worker().is_cancelled or self._interface_closing:
                return
            self.st, self.merged = origin, merged
            if not merged:
                self.st["scenario"] = config.options.scenario
            self._session_admitted = not merged
            self.origin_ready = True
            self.start_pages()

    async def choose_scenario(self, scenario):
        self.scenario = scenario
        with self.scenario_scope():
            self.profile_values = active_values()
        await self.recompose()
        if self._setup_first_run:
            self.open_setup(initial=True)
        else:
            self.initialize_origin()

    async def action_select_scenario(self, scenario):
        if self.scenario == "ask":
            self.main_query_one("#scenario-" + scenario).focus()

    def start_pages(self):
        if self.merged:
            from travel_mode.session_conflict import describe

            self.logline("warn", describe(self.st))
        if system_apps.ensure_conf():
            self.logline("dim", t("impl.created_default_action_configuration", v0=common.CONF_PATH))
        self.refresh_dash()
        self.refresh_battery()
        if self._battery_timer is None:
            self._battery_timer = self.set_interval(30, self.refresh_battery)

    def action_refresh(self):
        if self._interface_closing:
            return
        if self.scenario == "ask" or (not self.origin_ready and self._origin_error is None):
            return
        if self._mutation_lock.locked() and self._mutation_action is not None:
            self._refresh_after_action = True
            return
        self._refresh_generation += 1
        self.refresh_complete = False
        self.logline("dim", t("impl.refresh"))
        self.refresh_dash()
        self.refresh_battery()


def tui(return_class=False, dry_run=False, measure=None):
    if return_class:
        if not dry_run:
            return TravelApp

        class DryRunTravelApp(TravelApp):
            def __init__(self):
                super().__init__(dry_run=True)

        return DryRunTravelApp
    from travel_mode.paths import config_path

    return TravelApp(dry_run=dry_run, measure=measure, first_run=not config_path().exists()).run()


def run_tui():
    return tui()

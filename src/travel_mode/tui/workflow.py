"""Plan → results → session presentation over the shared Action executor."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Checkbox, Header, RichLog, Static

from travel_mode.i18n import t
from travel_mode.tui.plan_row import EvidenceLine
from travel_mode.tui.widgets import ActionFooter, Button


class Workflow:
    def compose(self):
        header = Header(show_clock=False)
        header.icon = ""
        yield header
        if self.scenario == "ask":
            from .scenario import ScenarioCard

            with VerticalScroll(id="scenario-picker"):
                yield Static(t("scenario.prompt"), classes="heading")
                yield Static(t("redesign.picker_hint"), classes="muted")
                yield ScenarioCard("work", 1)
                yield ScenarioCard("away", 2)
            yield Static(t("redesign.footer"), id="key-footer")
            return
        yield Static("", id="screen-heading", classes="heading")
        yield Static("", id="dash-battery", classes="muted")
        with VerticalScroll(id="body"):
            with Vertical(id="sleep-ready"):
                yield Static(t("redesign.attention"), classes="heading")
            yield Static("", id="session-summary")
            yield Vertical(id="restore-pending")
            with Horizontal(id="session-revived-line", classes="attention-line"):
                yield Static("", id="session-revived", classes="grow")
                yield Button(t("resurrection.reapply"), id="b-reapply")
            yield Vertical(id="dash-health")
            with Horizontal(id="filters"):
                for group in ("all", "power", "apps", "services", "network"):
                    yield Button(t("redesign." + group), id="filter-" + group)
            yield Vertical(Static(t("impl.scanning"), classes="muted"), id="plan-list")
            yield Button("", id="noise-summary", classes="muted")
            yield Static(t("redesign.keepalive"), id="keepalive-note", classes="muted")
            yield Checkbox(
                t("measurement.checkbox"),
                self.config.automatic_measurement if self.measure_enabled is None else self.measure_enabled,
                id="q-measure",
            )
        yield Static("", id="measurement-progress", classes="muted")
        yield Static("", id="result-summary")
        with Horizontal(id="utilities"):
            yield Button(t("redesign.more"), id="b-more")
            yield Button(t("setup.adjust"), id="b-setup")
            yield Button(t("health.refresh"), id="b-doctor")
        with Horizontal(id="primary-bar"):
            yield Button(t("redesign.apply", n=0), id="b-quick", variant="primary", disabled=True)
            yield Button(t("redesign.off"), id="b-off", variant="primary")
            yield Button(t("redesign.done"), id="b-complete", variant="primary")
        with Vertical(id="details-drawer"):
            yield Static("", id="row-evidence")
            yield RichLog(id="log", markup=True, max_lines=400, wrap=True)
            yield Static("", id="dash-audit")
        yield ActionFooter(self.last_result, id="last-result")
        yield Static(t("redesign.footer"), id="key-footer")

    def on_resize(self, event):
        screen = self.screen_stack[0]
        screen.set_class(event.size.width <= 80, "compact")
        short = event.size.height <= 26
        changed = screen.has_class("short") != short
        screen.set_class(short, "short")
        if changed and self.plan_data and not self._recomposing:
            self.run_worker(self.render_observations(), group="attention", exclusive=True)

    def init_workflow(self):
        from travel_mode.measurement import PassiveComparison

        self.passive_measurement = PassiveComparison()
        self._plan_render_lock = asyncio.Lock()
        self.view_mode = "plan"
        self._preparing_new_plan = False
        self.new_plan_complete = asyncio.Event()
        self.plan_items = []
        self.plan_data = None
        self.active_filter = "all"
        self.details_open = False
        self.focused_plan_id = None
        self.focused_evidence = None
        self.show_normal_checks = False
        self.show_suggestions = False
        self.last_result = ""
        self.current_battery = None
        self.current_battery_at = None
        self.current_estimate = None
        self.last_batch = dict(ok=0, failed=0)
        self.batch_running = False

    def update_result_summary(self):
        rows = [row for row in self.plan_items if row.get("result_member", False)]
        self.last_batch = dict(
            ok=sum(row["state"] in {"done", "warning"} for row in rows),
            failed=sum(row["state"] == "failed" for row in rows),
        )
        if self.batch_running:
            message = t("redesign.result_running", total=len(rows), completed=sum(self.last_batch.values()))
        else:
            message = t("redesign.result_summary", **self.last_batch)
            if any(row["state"] in {"failed", "warning"} for row in rows):
                from travel_mode.failures import log_path

                message += t("redesign.result_details", path=log_path())
        self.main_query_one("#result-summary", Static).update(message)
        return message

    def publish_result_summary(self, *_):
        message = self.update_result_summary()
        if message != self.last_result:
            self.logline("bad" if self.last_batch["failed"] else "ok", message)

    def update_primary(self):
        if self.scenario == "ask" or self._recomposing:
            return
        n = sum(
            row["selected"] and row["state"] == "planned" and row["action"] not in {"ask", "skip"}
            for row in self.plan_items
        )
        button = self.main_query_one("#b-quick", Button)
        button.label = t("redesign.apply", n=n)
        button.disabled = n == 0 or not self.origin_ready or self._mutation_lock.locked()
        button.display = self.view_mode == "plan"
        self.main_query_one("#b-off").display = self.view_mode == "session"
        self.main_query_one("#b-complete").display = self.view_mode == "result"
        self.main_query_one("#b-complete").disabled = any(r["state"] == "running" for r in self.plan_items)
        for selector in ("#filters", "#q-measure", "#keepalive-note"):
            self.main_query_one(selector).display = self.view_mode == "plan"
        self.main_query_one("#result-summary").display = self.view_mode == "result"
        self.main_query_one("#session-summary").display = self.view_mode == "session"
        self.main_query_one("#restore-pending").display = self.view_mode == "session"
        self.main_query_one("#details-drawer").display = self.details_open
        self.main_query_one("#plan-list").display = self.view_mode != "session"
        self.main_query_one("#sleep-ready").display = self.view_mode != "result"
        self.main_query_one("#dash-health").display = self.view_mode != "result"
        self.main_query_one("#noise-summary").display = self.view_mode == "plan" and bool(
            self.plan_data and any(self.plan_data["noise"].values())
        )
        self.main_query_one("#b-reapply").display = bool(self.resurrected) and self.view_mode == "session"
        self.main_query_one("#session-revived-line").display = (
            bool(self.resurrected) and self.view_mode == "session"
        )
        self.main_query_one("#screen-heading", Static).update(
            t(
                "scenario."
                + (
                    (self.st.get("scenario") or self.scenario)
                    if self.view_mode == "session"
                    else self.scenario
                )
            )
            + " · "
            + t("redesign." + self.view_mode)
        )

    async def render_plan(self):
        async with self._plan_render_lock:
            await self._render_plan()

    async def refresh_row(self, row):
        """Execution changes one row; filters and fresh scans rebuild the list."""
        from .plan_row import PlanRow

        async with self._plan_render_lock:
            widget = self.main_query_one("#row-" + row["id"], PlanRow)
            focused = self.focused
            restore_focus = focused is widget or (focused is not None and widget in focused.ancestors)
            await widget.recompose()
            if restore_focus:
                widget.focus(scroll_visible=False)
            self.update_primary()
            self.update_details()

    async def _render_plan(self):
        from .plan_row import PlanRow

        box = self.main_query_one("#plan-list")
        focused = self.focused
        focus_key = next(
            (
                node.model["id"]
                for node in ([focused, *focused.ancestors] if focused else [])
                if isinstance(node, PlanRow)
            ),
            None,
        )
        widgets = []
        for group in ("power", "apps", "services", "network"):
            matching = [
                row
                for row in self.plan_items
                if row["group"] == group and (self.view_mode != "result" or row.get("result_member", False))
            ]
            if not matching and (self.view_mode != "plan" or not self.plan_data["satisfied"][group]):
                continue
            title = Static(t("redesign." + group), classes="heading group-heading")
            title.display = self.size.height > 26 and self.active_filter in {"all", group}
            widgets.append(title)
            for row in matching:
                widget = PlanRow(row)
                widget.display = self.active_filter in {"all", group}
                widgets.append(widget)
            if self.view_mode == "plan" and self.plan_data["satisfied"][group]:
                from .view_models import satisfied_lines

                for text in satisfied_lines(self.plan_data["satisfied"][group]):
                    note = Static(Text(text), classes="muted satisfied")
                    note.display = self.active_filter in {"all", group}
                    widgets.append(note)
        with self.batch_update():
            await box.remove_children()
            await box.mount(*widgets)
        if focus_key:
            replacement = next(
                (
                    widget
                    for widget in widgets
                    if isinstance(widget, PlanRow) and widget.model["id"] == focus_key and widget.display
                ),
                None,
            )
            if replacement:
                replacement.focus(scroll_visible=False)
        for group in ("all", "power", "apps", "services", "network"):
            self.main_query_one("#filter-" + group).set_class(self.active_filter == group, "active-filter")
        if self.view_mode == "result":
            self.update_result_summary()
        self.update_primary()
        self.update_details()

    async def render_readiness(self):
        from travel_mode.flows.doctor import action_label

        rows = self.plan_data["readiness"]["items"]
        if self.size.height <= 26:
            rows = [row for row in rows if row["level"] in {"red", "green"}]
        box = self.main_query_one("#sleep-ready")
        widgets = [Static(t("redesign.attention"), classes="heading")]
        for index, row in enumerate(rows):
            mark = {"red": "✗", "amber": "▲", "green": "✓", "unknown": "?"}[row["level"]]
            line = [Static(escape(mark + " " + row["message"]), classes="grow " + row["level"])]
            if row["action"]:
                button = Button(action_label(row["action"]), id=row.get("button_id", f"ready-{index}"))
                button.health_descriptor = row["action"]
                line.append(button)
            widgets.append(
                EvidenceLine(
                    *line, name=t("redesign.attention"), evidence=[str(row)], classes="attention-line"
                )
            )
        await box.remove_children()
        await box.mount(*widgets)

    async def render_restore_pending(self):
        from travel_mode.actions.privilege_plan import pending_restore

        box = self.main_query_one("#restore-pending")
        widgets = []
        for row in pending_restore(self.st):
            widgets.append(
                Horizontal(
                    Static(
                        "✗ " + t("admin.pending_item", name=row["name"], reason=row["reason"]), classes="grow"
                    ),
                    Button(t("admin.retry"), id="restore-retry-" + row["key"]),
                    classes="attention-line",
                )
            )
        await box.remove_children()
        if widgets:
            await box.mount(*widgets)

    async def render_attention(self):
        from travel_mode.flows.doctor import action_label

        box = self.main_query_one("#dash-health")
        widgets = []
        from .plan import finding_covered

        findings = [
            row
            for row in self.plan_data["attention"]
            if not finding_covered(row, self.plan_items, session=self.view_mode == "session")
        ]
        compact_readiness = []
        short = self.size.height <= 26
        if short:
            compact_readiness = [
                row for row in self.plan_data["readiness"]["items"] if row["level"] not in {"red", "green"}
            ]
            count = len(findings) + len(compact_readiness)
            if count:
                widgets.append(
                    Button(
                        t(
                            "redesign.suggestions",
                            n=count,
                            toggle=t("redesign.collapse" if self.show_suggestions else "redesign.expand"),
                        ),
                        id="b-suggestions",
                        classes="muted",
                    )
                )
            if not self.show_suggestions:
                findings, compact_readiness = [], []
        for row in compact_readiness:
            line = [Static(escape("▲ " + row["message"]), classes="grow")]
            if row["action"]:
                button = Button(action_label(row["action"]), id=row["button_id"])
                button.health_descriptor = row["action"]
                line.append(button)
            widgets.append(
                EvidenceLine(
                    *line, name=t("redesign.attention"), evidence=[str(row)], classes="attention-line"
                )
            )
        for row in findings:
            caffeinate = [d for d in row["available_actions"] if d["kind"] == "caffeinate"]
            for descriptor in caffeinate:
                button = Button(
                    action_label(descriptor), id="b-killcaff-" + str(descriptor["process"]["pid"])
                )
                button.health_descriptor = descriptor
                widgets.append(
                    EvidenceLine(
                        Static(escape(row["summary"]), classes="grow"),
                        button,
                        name="caffeinate",
                        evidence=[str(descriptor)],
                        classes="attention-line",
                    )
                )
            if caffeinate and len(caffeinate) == len(row["available_actions"]):
                continue
            line = [Static(escape(row["summary"]), classes="grow")]
            if row["available_actions"]:
                descriptor = row["available_actions"][0]
                button = Button(action_label(descriptor), id="b-health-" + row["id"] + "-0")
                button.health_descriptor = descriptor
                line.append(button)
            widgets.append(
                EvidenceLine(*line, name=row["title"], evidence=row["evidence"], classes="attention-line")
            )
        if self.plan_data["normal"] and (not short or self.show_suggestions):
            widgets.append(
                Button(t("redesign.normal", n=self.plan_data["normal"]), id="b-normal", classes="muted")
            )
        if self.show_normal_checks:
            from travel_mode.health_view import presentation

            widgets.extend(
                Static(escape(row["summary"]), classes="muted")
                for row in presentation(self.plan_data["health"], scenario=self.scenario)
                if row["state"] == "normal"
            )
        await box.remove_children()
        await box.mount(*widgets)
        await self.render_restore_pending()

    async def render_observations(self):
        # Selection changes only the presentation; never repeat discovery.
        async with self._plan_render_lock:
            await self.render_readiness()
            await self.render_attention()

    @work(exclusive=True, group="dash")
    async def refresh_dash(self):
        from textual.worker import get_current_worker

        from travel_mode.resurrection import detect

        from .plan import collect

        generation = self._refresh_generation
        self.refresh_complete = False
        self.pages_ready = set()
        with self.scenario_scope():
            try:
                view = self.display_state()
                data = await self.in_thread(collect, self.scenario, view)
                revived = await self.in_thread(detect, view)
                async with self._refresh_lock:
                    if (
                        get_current_worker().is_cancelled
                        or generation != self._refresh_generation
                        or self._interface_closing
                    ):
                        return
                    from travel_mode.sleep_readiness import store

                    store(data["readiness"])
                    self.plan_data = data
                    self.scan, self.svc = data["scan"], data["services"]
                    self.resurrected = revived
                    self._observed_resurrections.extend(revived)
                    if self.view_mode != "result":
                        saved = {row["id"]: row for row in self.plan_items}
                        self.plan_items = data["rows"]
                        for row in self.plan_items:
                            previous = saved.get(row["id"])
                            if previous and previous["state"] == "planned":
                                row.update(selected=previous["selected"], action=previous["action"])
                        if self.merged and not self._session_admitted and not self._preparing_new_plan:
                            self.view_mode = "session"
                    if not self._mutation_lock.locked():
                        self.flush_session_observations()
                    self.main_query_one("#noise-summary", Button).label = t(
                        "redesign.noise",
                        apps=len(data["noise"]["apps"]) + len(data["noise"]["selectors"]),
                        services=len(data["noise"]["services"]),
                        network=len(data["noise"]["network"]),
                    )
                    await self.render_readiness()
                    await self.render_attention()
                    await self.render_plan()
                    self.update_session()
                    self.pages_ready = {"plan", "session", "health"}
                    self.refresh_complete = True
                    from .messages import PageRefreshed

                    self.post_message(PageRefreshed("plan"))
            except Exception as exc:
                if get_current_worker().is_cancelled:
                    return
                self.report_action_error(exc, t("impl.refresh"))
                raise

    def action_goto(self, pane):
        # Keyboard 1–5 now filter the same canonical list.
        group = {
            "p-dash": "all",
            "p-power": "power",
            "p-apps": "apps",
            "p-svc": "services",
            "p-net": "network",
        }.get(pane, pane)
        if self.scenario == "ask":
            self.main_query_one("#scenario-" + ("away" if pane == "p-power" else "work")).focus()
            return
        self.set_filter(group)

    def set_filter(self, group):
        if group not in {"all", "power", "apps", "services", "network"}:
            raise ValueError(f"Unknown filter: {group}")
        self.active_filter = group
        for widget in self.main_query("PlanRow"):
            widget.display = group in {"all", widget.model["group"]}
        # Headings and satisfied summaries are rebuilt from the same model.
        if self.plan_data:
            self.run_worker(self.render_plan(), group="filter", exclusive=True)

    def update_details(self):
        from .view_models import evidence_lines

        if self.scenario == "ask" or self._recomposing:
            return
        row = next((r for r in self.plan_items if r["id"] == self.focused_plan_id), None)
        if row is None:
            row = self.focused_evidence
        self.main_query_one("#row-evidence", Static).update(
            t("redesign.drawer_heading", name=row["name"]) + "\n" + "\n".join(evidence_lines(row))
            if row
            else t("redesign.no_details")
        )

    def action_details(self):
        if self.scenario == "ask":
            return
        self.details_open = not self.details_open
        self.main_query_one("#details-drawer").display = self.details_open
        self.update_details()

    def action_help(self):
        from .session_screen import SessionScreen

        self.push_screen(SessionScreen(t("redesign.help_text")))

    async def _dispatch_button(self, event):
        from .plan import item

        bid = event.button.id or ""
        if bid == "b-quick":
            await self.run_plan(event.button)
        elif bid == "b-off":
            await self.act_off(event.button)
            self.view_mode = "session" if self.merged else "plan"
            self.update_primary()
        elif bid.startswith("restore-retry-"):
            await self.act_off(event.button, only={bid.removeprefix("restore-retry-")})
            self.view_mode = "session" if self.merged else "plan"
            self.update_primary()
        elif bid == "b-reapply":
            from travel_mode.resurrection import reapply

            await self.busy(event.button, reapply, self.st, self.execute, self.save)
            self._transaction_view = deepcopy(self.st)
            self.action_refresh()
        elif bid.startswith(("ready-", "b-health-", "b-killcaff-")):
            descriptor = event.button.health_descriptor
            rows = [
                item(f"health-action-{index}", "health", str(event.button.label), "apply", "", target)
                for index, target in enumerate(descriptor.get("targets", [descriptor]))
            ]
            await self.execute_rows(event.button, rows, measure=False)
            self._transaction_view = deepcopy(self.st)
            self.action_refresh()
        elif bid.startswith(("retry-", "force-")):
            mode, key = bid.split("-", 1)
            row = next(row for row in self.plan_items if row["id"] == key)
            row.update(
                action="force" if mode == "force" else row.get("retry_action", row["action"]),
                state="planned",
                selected=True,
            )
            await self.execute_rows(event.button, [row], measure=False)
            await self.render_plan()
        elif bid.startswith("thaw-"):
            path = event.button.app_path
            row = item(
                "thaw-app",
                "apps",
                event.button.app_name,
                "thaw",
                "",
                dict(kind="app", path=path, name=event.button.app_name),
            )
            await self.execute_rows(event.button, [row], measure=False)

    async def _handle_button_pressed(self, event):
        bid = event.button.id or ""
        if bid.startswith("filter-"):
            self.set_filter(bid.removeprefix("filter-"))
            return
        if bid.startswith("cycle-"):
            key = bid.removeprefix("cycle-")
            row = next(row for row in self.plan_items if row["id"] == key)
            if row["state"] == "planned":
                actions = ("freeze", "quit", "ask", "skip")
                row["action"] = actions[(actions.index(row["action"]) + 1) % len(actions)]
                row["selected"] = row["action"] in {"freeze", "quit"}
                await self.render_plan()
                await self.render_observations()
            return
        if bid == "b-complete":
            if self._mutation_lock.locked():
                return
            self.view_mode = "session" if self.merged else "plan"
            self.update_primary()
            self.update_session()
            self.main_query_one("#body").focus()
            return
        if bid == "b-suggestions":
            self.show_suggestions = not self.show_suggestions
            await self.render_attention()
            return
        if bid == "b-normal":
            self.show_normal_checks = not self.show_normal_checks
            await self.render_attention()
            return
        if bid == "noise-summary":
            self.details_open = True
            self.main_query_one("#details-drawer").display = True
            import json

            self.main_query_one("#row-evidence", Static).update(
                json.dumps(self.plan_data["noise"], ensure_ascii=False, indent=2)
            )
            return
        if bid == "b-more":
            self.open_more()
            return
        if bid == "b-new-plan":
            self.view_mode = "plan"
            self.plan_items = []
            self.action_refresh()
            return
        if bid in {"b-measure", "b-energy", "b-thaw", "b-automation"}:
            await self.utility_action(bid)
            return
        await super()._handle_button_pressed(event)

    async def run_plan(self, button):
        for row in self.plan_items:
            row["result_member"] = False
        selected = [
            row
            for row in self.plan_items
            if row["state"] == "planned" and row["selected"] and row["action"] not in {"ask", "skip"}
        ]
        from travel_mode.failures import BatchStopped

        measure = self.main_query_one("#q-measure", Checkbox).value
        for row in selected:
            row["result_member"] = True
        self.batch_running = True
        self.view_mode = "result"
        await self.render_plan()
        try:
            await self.execute_rows(button, selected, measure=measure)
        except BatchStopped:
            self.update_details()  # busy() already persisted and displayed the stopping error.
        for row in self.plan_items:
            if row["state"] == "planned":
                row.update(state="skipped", selected=False, result=t("redesign.row_skipped"), operations=[])
        self.view_mode = "result"
        await self.render_plan()
        self.main_query_one("#body").focus()
        self.action_refresh()

    async def execute_rows(self, button, rows, *, measure):
        from .plan import success

        notices = []
        self.last_batch = dict(ok=0, failed=0)
        self.batch_running = True

        async def updated(row):
            if any(r is row for r in self.plan_items):
                await self.refresh_row(row)
                self.update_result_summary()

        def flow():
            from travel_mode.system.shell import is_dry_run

            comparison = self.passive_measurement.begin() if measure and not is_dry_run() else None
            from .plan import execution_units

            units = list(execution_units(rows, self.scenario, self.st, self.save))
            for unit, _action in units:
                for row in unit:
                    row.update(state="running", operations=[], result_member=True)
                    self.call_from_thread(updated, row)

            def completed(index, result):
                unit, action = units[index]
                for row in unit:
                    ok = success(result)
                    forced = getattr(result, "forced", False) or getattr(result, "helpers_remaining", False)
                    row.update(
                        state="warning" if ok and forced else "done" if ok else "failed",
                        app_running=not ok and row["descriptor"]["kind"] == "app",
                    )
                    if not ok and row["descriptor"]["kind"] == "app":
                        from travel_mode.failures import probe
                        from travel_mode.system.apps import app_running

                        row["app_running"] = bool(
                            probe(
                                self.st,
                                row["name"],
                                app_running,
                                row["descriptor"]["path"],
                                save=self.save,
                                emit=self.tlog,
                            )
                        )
                    if ok:
                        if forced:
                            row["warning_key"] = (
                                "failure.forced_"
                                + getattr(getattr(result, "normal", None), "status", "error")
                                if getattr(result, "forced", False)
                                else "failure.quit_helpers"
                            )
                        self.save()
                        row["result"] = (
                            result.explanation(row["name"])
                            if forced
                            else t(
                                "redesign.row_thawed"
                                if row["action"] == "thaw"
                                else "app_view.frozen"
                                if row["action"] == "freeze"
                                else "app_view.exited"
                            )
                            if row["descriptor"]["kind"] == "app"
                            else t("redesign.row_done")
                        )
                        from travel_mode.system.shell import is_dry_run

                        if is_dry_run():
                            from travel_mode.presentation import action_message
                            from travel_mode.system.apps import freeze_result_count

                            descriptor = row["descriptor"]
                            if descriptor["kind"] == "app":
                                row["result"] = (
                                    action_message(
                                        "impl.frozen_processes_stopped_off_resumes_them",
                                        v0=row["name"],
                                        v1=freeze_result_count(descriptor["path"]),
                                    )
                                    if row["action"] == "freeze"
                                    else action_message("impl.quit", v0=row["name"])
                                )
                            elif descriptor["kind"] == "agent":
                                row["result"] = action_message("impl.unloaded", v0=row["name"])
                            else:
                                row["result"] = t("redesign.planned_short")
                            row["planned_result"] = True
                        self.last_batch["ok"] += 1
                        if not forced:
                            self.tlog("ok", row["name"] + ": " + row["result"])
                        if row["descriptor"]["kind"] == "app" and row["action"] in {"quit", "freeze"}:
                            notices.append((row["name"], row["action"], row["default_action"]))
                    else:
                        from travel_mode.failures import action_identity

                        failures = [
                            receipt
                            for receipt in self.st.get("action_failures", [])
                            if receipt.get("identity") == action_identity(action)
                        ]
                        row["result"] = failures[-1]["reason"] if failures else t("failure.incomplete")
                        row["retry_action"] = row["action"]
                        row["quit_status"] = getattr(getattr(result, "normal", result), "status", "error")
                        if failures:
                            import json

                            failure = failures[-1]
                            command = failure["command"]
                            row.setdefault("detail_fields", {}).update(
                                command="osascript quit"
                                if command[:1] == ["osascript"] and any(" to quit" in arg for arg in command)
                                else " ".join(command),
                                returncode=failure["rc"],
                            )
                            import re

                            event_code = re.search(r"\((-128|-1712|-600)\)", failure["stderr"])
                            if event_code:
                                row["detail_fields"]["apple_event"] = int(event_code[1])
                            row["evidence"].insert(0, json.dumps(failure, ensure_ascii=False))
                        self.last_batch["failed"] += 1
                    self.call_from_thread(updated, row)

            from travel_mode.actions.base import Context
            from travel_mode.failures import reporter

            with reporter(self.action_notice):
                self.executor.execute_all(
                    [action for _, action in units], Context(self.st, self.save), completed=completed
                )
            if comparison is not None and "session" in self.st:
                from datetime import datetime

                self.passive_measurement.complete(datetime.now().astimezone())
                self.st["measurement"] = comparison
                self.save()

        try:
            await self.busy(button, flow)
        finally:
            for row in rows:
                if row["state"] == "running":
                    row.update(state="failed", result=self.last_result)
                    self.last_batch["failed"] += 1
            self.batch_running = False
            self.update_result_summary()
            self.update_primary()
        for name, action, default in notices:
            self.offer_remember(name, action, default)

    def battery_heading(self, reading):
        from travel_mode.status import power_line

        percent = (
            round(reading["remaining_mah"] / reading["maximum_mah"] * 100)
            if reading.get("maximum_mah")
            else None
        )
        source = (
            "ac"
            if reading.get("external_connected") or reading.get("reason") in {"charging", "external_power"}
            else "battery"
        )
        return power_line(
            dict(
                power_source=source,
                battery=dict(
                    percent=percent, charging_state="charging" if reading.get("is_charging") else None
                ),
            )
        )

    def update_session(self):
        if self.scenario == "ask":
            return
        from travel_mode.system.battery import describe_interval

        from .session_view import session_view

        view = session_view(
            self.st,
            self.current_battery,
            reading_at=self.current_battery_at,
            estimate=self.current_estimate
            or dict(
                average_watts=None,
                remaining_minutes=None,
                estimate_source=None,
                estimate_error=describe_interval(),
            ),
        )
        self.session_view = view
        self.main_query_one("#session-summary", Static).update("\n".join(view["lines"]))
        if self.view_mode == "session" and view["title"]:
            self.main_query_one("#screen-heading", Static).update(view["title"])
        self.main_query_one("#session-revived", Static).update(
            t(
                "redesign.revived",
                n=len(self.resurrected),
                names=", ".join(row.get("name", row.get("label", "")) for row in self.resurrected),
            )
        )

    def open_more(self):
        from .utility_screen import UtilityScreen

        self.push_screen(
            UtilityScreen(self.config.menubar.show),
            lambda action: self.run_worker(self.utility_action(action), group="utility") if action else None,
        )

    async def utility_action(self, action):
        if action == "b-menubar":
            from travel_mode.config import load_config, write_menubar
            from travel_mode.paths import config_path

            async with self._mutation_lock:
                current = load_config(config_path()).menubar.show
                show = "session" if current == "always" else "always"
                await self.in_thread(write_menubar, show)
                self.config = load_config(config_path())
                self.notify(t("menubar.mode_setting", mode=t("menubar.mode_" + show)))
        elif action == "b-new-plan":
            from .scenario import ScenarioPicker

            self.new_plan_complete.clear()

            def chosen(scenario):
                if scenario is not None:
                    self.run_worker(self.prepare_new_plan(scenario), group="new-plan", exclusive=True)
                else:
                    self.new_plan_complete.set()

            await self.push_screen(ScenarioPicker(), chosen)
        elif action in {"b-category-sync", "b-category-updater"}:
            categories = {"sync", "backup"} if action == "b-category-sync" else {"updater"}
            self.view_mode = "plan"
            self.active_filter = "all"
            for row in self.plan_items:
                if row["state"] == "planned" and row.get("category") in categories:
                    row["selected"] = True
                    if row["action"] in {"ask", "skip"}:
                        row["action"] = "quit"
            await self.render_plan()
        elif action == "b-history":
            await self.show_history()
        elif action == "b-setup":
            self.open_setup()
        elif action == "b-energy":
            self.details_open = True
            self.main_query_one("#details-drawer").display = True
            self.refresh_audit_detail()
        elif action == "b-measure":
            self.measure_now()
        elif action == "b-automation":
            import json

            from travel_mode.automation import status

            from .session_screen import SessionScreen

            data = await self.in_thread(status)
            self.push_screen(SessionScreen(json.dumps(data, ensure_ascii=False, indent=2)))
        elif action == "b-thaw":
            from .utility_screen import ThawScreen

            self.push_screen(ThawScreen(self.st.get("frozen_apps", [])))

    async def prepare_new_plan(self, scenario):
        try:
            async with self._mutation_lock:
                self.scenario = scenario
                with self.scenario_scope():
                    from travel_mode.profiles import active_values

                    self.profile_values = active_values()
                self.view_mode = "plan"
                self.plan_items = []
                self.active_filter = "all"
                self._session_admitted = False
                self._preparing_new_plan = True
                from travel_mode.measurement import PassiveComparison

                self.passive_measurement = PassiveComparison()
                self.current_estimate = None
            self.action_refresh()
        finally:
            self.new_plan_complete.set()

    @work(exclusive=True, group="power-measure")
    async def measure_now(self):
        from travel_mode import measurement

        with self.scenario_scope():
            clock = measurement.CancellableClock(self._measurement_cancelled)
            result = await self.in_thread(
                lambda: measurement.measure(
                    self.config.measurement,
                    clock=clock,
                    progress=lambda text: self.call_from_thread(self.update_measurement_progress, text),
                )
            )
            from .session_screen import SessionScreen

            self.push_screen(SessionScreen(measurement.describe(result)))

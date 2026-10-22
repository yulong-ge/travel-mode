"""Finite setup UI: one discovery, editable scenario columns, explicit save."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from rich.cells import cell_len
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from travel_mode import app_policy, onboarding
from travel_mode.config import load_config
from travel_mode.flows import setup
from travel_mode.i18n import t
from travel_mode.paths import config_path


class ConfirmRemember(ModalScreen):
    DEFAULT_CSS = """
    ConfirmRemember { align: center middle; background: $background 80%; }
    ConfirmRemember > Vertical { width: 90%; height: auto; border: solid $accent; padding: 1 2; }
    ConfirmRemember Horizontal { height: auto; }
    """

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self):
        with Vertical():
            yield Label(self.text, markup=False)
            with Horizontal():
                yield Button(t("setup.yes"), id="remember-yes", variant="primary")
                yield Button(t("setup.no"), id="remember-no")

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "remember-yes")


class SetupRow(Button):
    """A focusable single-line action row, with immediate keyboard scrolling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Button's default line padding offsets the table columns. CSS requires >0.
        self.styles.line_pad = 0

    def update_label(self, text):
        # Content equality compares plain text only; scenario switches change just spans.
        self.set_reactive(Button.label, self.validate_label(text))
        self.mutate_reactive(Button.label)


class SetupGroup(Button):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.styles.line_pad = 0


class SetupScreen(ModalScreen):
    BINDINGS: ClassVar = [
        Binding("escape", "cancel", show=False),
        Binding("space", "cycle", show=False),
        Binding("left", "column('work')", show=False, priority=True),
        Binding("right", "column('away')", show=False, priority=True),
        Binding("up", "move(-1)", show=False, priority=True),
        Binding("down", "move(1)", show=False, priority=True),
        Binding("home", "edge(False)", show=False, priority=True),
        Binding("end", "edge(True)", show=False, priority=True),
    ]
    DEFAULT_CSS = """
    SetupScreen { background: $surface; }
    #setup-title { text-style: bold; height: 1; margin: 0; padding: 0 1; }
    #setup-help { width: 1fr; height: auto; padding: 0 1; }
    #setup-detected { height: 1; padding: 0 1; }
    #setup-readiness { height: auto; padding: 0 1; }
    #setup-readiness Static { height: 1; }
    .setup-header { height: 1; color: $text-muted; }
    #setup-body { height: 1fr; padding: 0 1; scrollbar-size: 1 1; }
    #setup-rows { height: auto; }
    SetupScreen Button.setup-app {
        width: 1fr; height: 1; min-height: 1; min-width: 0;
        border: none; padding: 0; margin: 0;
        text-style: none; color: $text; background: $surface;
        text-align: left; content-align: left middle;
    }
    SetupScreen Button.setup-app:focus, SetupScreen Button.setup-app:hover {
        border: none; background: $primary-muted; background-tint: transparent;
        text-style: none;
    }
    SetupScreen Button.setup-group {
        width: 1fr; min-width: 0; height: 1; min-height: 1; border: none;
        padding: 0; margin: 0; text-align: left; content-align: left middle;
        text-style: none; color: $text; background: $surface;
    }
    SetupScreen Button.setup-group:focus, SetupScreen Button.setup-group:hover {
        background: $surface; background-tint: transparent; text-style: underline;
    }
    .setup-items { height: auto; }
    .setup-done { display: none; }
    #setup-controls { height: 3; min-height: 3; padding: 0 1; }
    #setup-controls Button { height: 3; min-height: 3; min-width: 0; width: 1fr; margin: 0 1 0 0; }
    #setup-controls Button:focus { text-style: bold underline; }
    #setup-controls Button:disabled {
        background: $surface; color: $text-muted; text-opacity: 1;
    }
    #setup-status { height: auto; max-height: 6; margin: 1 0; padding: 0 1; overflow-y: auto; }
    """

    def __init__(self, *, dry_run=False, stay_open=False):
        super().__init__()
        self.dry_run = dry_run
        self.stay_open = stay_open
        self.rows = []
        self.selected = {"work": {}, "away": {}}
        self.column = "work"
        self.ready = asyncio.Event()
        self.completed = asyncio.Event()
        self.reviewed = asyncio.Event()
        self.changed = asyncio.Event()
        self.focus_committed = asyncio.Event()
        self._choice_generation = 0
        self.expanded = asyncio.Event()
        self.data = None
        self.initial_selected = None

    def compose(self) -> ComposeResult:
        yield Label(t("setup.title"), id="setup-title")
        yield Label(t("setup.keys", scenario=t("scenario." + self.column)), id="setup-help")
        yield Static(id="setup-detected", markup=False)
        yield Vertical(id="setup-readiness")
        with VerticalScroll(id="setup-body"):
            yield Vertical(id="setup-rows")
        yield Static(t("impl.scanning"), id="setup-status", markup=False)
        with Horizontal(id="setup-controls"):
            yield Button(t("onboarding.recommend"), id="setup-recommend", disabled=True)
            yield Button(t("setup.column"), id="setup-column")
            yield Button(t("setup.review"), id="setup-review", variant="primary", disabled=True)
            yield Button(t("setup.skip"), id="setup-skip")
            yield Button(t("setup.confirm_save"), id="setup-save", variant="success", disabled=True)
            yield Button(t("onboarding.done"), id="setup-done", classes="setup-done")

    async def on_mount(self):
        try:
            from copy import deepcopy

            self.data = await asyncio.to_thread(onboarding.scan)
            self.rows = self.data["apps"]
            self.selected = onboarding.choices(self.data, load_config())
            self.initial_selected = deepcopy(self.selected)
            detected = self.query_one("#setup-detected", Static)
            detected.update(Text(onboarding.detected(self.data), no_wrap=True, overflow="ellipsis"))
            detected.tooltip = onboarding.detected(self.data)
            readiness = self.query_one("#setup-readiness")
            issues = [row for row in self.data["readiness"]["items"] if row["level"] != "green"]
            readiness.display = bool(issues)
            for row in issues:
                mark, style = ("✗", "red") if row["level"] == "red" else ("▲", "yellow")
                hint = Static(
                    Text(f"{mark} {row['message']}", style=style, no_wrap=True, overflow="ellipsis")
                )
                hint.tooltip = row["message"]
                await readiness.mount(hint)
            self.query_one("#setup-done").display = False
            widgets = []
            for group in onboarding.GROUPS:
                apps = [(i, row) for i, row in enumerate(self.rows) if row["group"] == group]
                services = [(i, row) for i, row in enumerate(self.data["services"]) if row["group"] == group]
                count = len(apps) + len(services)
                if not count:
                    continue
                widgets.append(
                    SetupGroup(
                        self.group_label(group),
                        id="setup-group-" + group,
                        classes="setup-group",
                        compact=True,
                    )
                )
                children = [Static(id="setup-header-" + group, classes="setup-header")]
                children.extend(
                    SetupRow(self.row_label(row), id=f"setup-app-{i}", classes="setup-app", compact=True)
                    for i, row in apps
                )
                children.extend(
                    SetupRow(
                        self.service_label(row), id=f"setup-service-{i}", classes="setup-app", compact=True
                    )
                    for i, row in services
                )
                content = Vertical(*children, id="setup-items-" + group, classes="setup-items")
                content.display = False
                widgets.append(content)
            await self.query_one("#setup-rows").mount(*widgets)
            self.query_one("#setup-recommend", Button).disabled = False
            self.update_table()
            self.query_one("#setup-status", Static).update("")
            self.query_one("#setup-status", Static).display = False
            self.query_one("#setup-review", Button).disabled = False
        except Exception as exc:  # noqa: BLE001 - UI boundary logs and explicitly presents every failure
            self.query_one("#setup-status", Static).update(setup.failure(exc))
        finally:
            self.ready.set()

    def action_cancel(self):
        self.dismiss(None)

    def action_column(self, scenario):
        self.column = scenario
        self.query_one("#setup-help", Label).update(t("setup.keys", scenario=t("scenario." + scenario)))
        self.update_table()
        self.call_after_refresh(self.reveal_focused_row)

    def set_focus(self, widget, scroll_visible=True, from_app_focus=False):
        # Own row scrolling so Textual's animated centering cannot race header reflow.
        row = isinstance(widget, SetupRow)
        if row:
            self.focus_committed.clear()
        super().set_focus(
            widget, scroll_visible=False if row else scroll_visible, from_app_focus=from_app_focus
        )
        if row:
            self.call_after_refresh(self.reveal_focused_row)

    def reveal_focused_row(self):
        row = self.focused
        if isinstance(row, SetupRow):
            self.focus_committed.clear()
            row.scroll_visible(animate=False, immediate=True)

            def committed():
                if self.is_attached and self.focused is row:
                    self.focus_committed.set()

            self.call_after_refresh(committed)

    def column_widths(self):
        # Reserve body padding and its scrollbar even when the list fits on screen.
        width = max(0, self.size.width - 4)
        names = [row["name"] for row in self.rows]
        names.extend(row["label"] for row in (self.data or {}).get("services", []))
        name_width = min(24, max([18, *(cell_len(name) for name in names)]))
        work_width = cell_len(t("scenario.work"))
        away_width = cell_len(t("scenario.away"))
        return width, name_width, work_width, away_width

    def table_line(self, name, work, away, reason, *, heading=False):
        width, name_width, work_width, away_width = self.column_widths()
        line = Text("   " if heading else "·  ", no_wrap=True, overflow="ellipsis")
        for value, size, scenario in (
            (name, name_width, None),
            (work, work_width, "work"),
            (away, away_width, "away"),
        ):
            style = ("bold underline" if scenario == self.column else "dim") if scenario else ""
            field = Text(value, style=style)
            field.truncate(size, overflow="ellipsis", pad=True)
            line.append_text(field)
            line.append(" ")
        line.append(reason)
        line.truncate(width, overflow="ellipsis")
        return line, width

    def row_label(self, row):
        reason = onboarding.reason(row)
        width, name_width, work_width, away_width = self.column_widths()
        if 3 + name_width + work_width + away_width + 3 + cell_len(reason) > width:
            reason = t("setup.short_" + row["reason"])
            if row.get("configured"):
                reason = t("onboarding.your_setting", reason=reason)
        line, _ = self.table_line(
            row["name"],
            t("action." + self.selected["work"][row["name"]]),
            t("action." + self.selected["away"][row["name"]]),
            reason,
        )
        return line

    def update_table(self):
        for header in self.query(".setup-header"):
            header.update(
                self.table_line(
                    t("onboarding.name"),
                    t("scenario.work"),
                    t("scenario.away"),
                    t("setup.reason"),
                    heading=True,
                )[0]
            )
        self.update_groups()
        for index, row in enumerate((self.data or {}).get("services", [])):
            button = self.query_one(f"#setup-service-{index}", SetupRow)
            button.update_label(self.service_label(row))
            button.tooltip = t(
                "onboarding.service",
                name=row["label"],
                action=t("onboarding." + self.selected["services"][row["label"]]),
                reason=onboarding.reason(row),
            )
        for index, row in enumerate(self.rows):
            button = self.query_one(f"#setup-app-{index}", SetupRow)
            button.update_label(self.row_label(row))
            button.tooltip = app_policy.row_text(row, self.selected)

    def on_resize(self):
        if self.ready.is_set() and self.data:
            self.update_table()
            self.call_after_refresh(self.reveal_focused_row)

    def service_label(self, row):
        action = t("onboarding." + self.selected["services"][row["label"]])
        return self.table_line(row["label"], action, action, onboarding.reason(row))[0]

    def group_label(self, group, expanded=False):
        apps = [row for row in self.rows if row["group"] == group]
        services = [row for row in self.data["services"] if row["group"] == group]
        selected = self.initial_selected
        actions = []
        for action in ("freeze", "quit", "ask", "skip"):
            count = sum(selected[self.column][row["name"]] == action for row in apps)
            if count:
                name = (
                    t("onboarding.keep") if action == "skip" and group == "network" else t("action." + action)
                )
                actions.append(t("onboarding.action_count", action=name, n=count))
        parts = []
        if actions:
            parts.append(
                t(
                    "onboarding.group_apps",
                    scenario=t("scenario." + self.column),
                    actions=t("onboarding.action_separator").join(actions),
                )
            )
        for action in ("unload", "keep"):
            count = sum(selected["services"][row["label"]] == action for row in services)
            if count:
                parts.append(t("onboarding.group_services", action=t("onboarding." + action), n=count))
        line = Text("▾ " if expanded else "▸ ", no_wrap=True, overflow="ellipsis")
        line.append(t("onboarding.group_" + group), "bold")
        line.append(t("onboarding.group_count", n=len(apps) + len(services)))
        line.append(
            " · " + t("onboarding.group_recommended", summary=t("onboarding.group_separator").join(parts)),
            "dim",
        )
        return line

    def update_groups(self):
        for button in self.query(SetupGroup):
            group = button.id.removeprefix("setup-group-")
            button.label = self.group_label(group, self.query_one("#setup-items-" + group).display)
            button.tooltip = button.label.plain

    def show_group(self, group):
        content = self.query_one("#setup-items-" + group)
        content.display = True
        self.update_groups()

    def invalidate(self):
        self._choice_generation += 1
        self.query_one("#setup-save", Button).disabled = True
        self.reviewed.clear()
        self.changed.set()

    def action_move(self, offset):
        rows = list(self.query(SetupRow))
        if not rows:
            return
        index = rows.index(self.focused) if self.focused in rows else -1
        row = rows[min(len(rows) - 1, max(0, index + offset))]
        self.show_group(row.parent.id.removeprefix("setup-items-"))
        row.focus()

    def action_edge(self, last):
        rows = list(self.query(SetupRow))
        if rows:
            row = rows[-1 if last else 0]
            self.show_group(row.parent.id.removeprefix("setup-items-"))
            row.focus()

    def cycle(self, index):
        self._choice_generation += 1
        row = self.rows[index]
        actions = ("freeze", "quit", "ask", "skip")
        current = self.selected[self.column][row["name"]]
        self.selected[self.column][row["name"]] = actions[(actions.index(current) + 1) % 4]
        button = self.query_one(f"#setup-app-{index}", SetupRow)
        button.update_label(self.row_label(row))
        button.tooltip = app_policy.row_text(row, self.selected)
        self.query_one("#setup-save", Button).disabled = True
        self.reviewed.clear()
        self.changed.set()

    def action_cycle(self):
        if self.focused and self.focused.id and self.focused.id.startswith("setup-service-"):
            self.focused.press()
            return
        if self.focused and self.focused.id and self.focused.id.startswith("setup-app-"):
            self.cycle(int(self.focused.id.removeprefix("setup-app-")))

    async def on_button_pressed(self, event):
        event.stop()
        key = event.button.id
        if key and key.startswith("setup-group-"):
            self.expanded.clear()
            content = self.query_one("#setup-items-" + key.removeprefix("setup-group-"))
            content.display = not content.display
            self.update_groups()
            self.call_after_refresh(self.expanded.set)
        elif key == "setup-recommend":
            from copy import deepcopy

            self.selected = deepcopy(self.initial_selected)
            self.update_table()
            self.invalidate()
        elif key and key.startswith("setup-service-"):
            row = self.data["services"][int(key.removeprefix("setup-service-"))]
            current = self.selected["services"][row["label"]]
            self.selected["services"][row["label"]] = "keep" if current == "unload" else "unload"
            self.update_table()
            self.invalidate()
        elif key == "setup-done":
            self.dismiss(t("setup.saved"))
        elif key and key.startswith("setup-app-"):
            self.cycle(int(key.removeprefix("setup-app-")))
        elif key == "setup-column":
            self.action_column("away" if self.column == "work" else "work")
        elif key == "setup-review":
            self.reviewed.clear()
            self.query_one("#setup-save", Button).disabled = True
            status = self.query_one("#setup-status", Static)
            status.styles.max_height = None
            status.update(onboarding.summary(self.selected, config_path()))
            status.display = True
            generation = self._choice_generation

            def enable_save():
                if self.is_attached and generation == self._choice_generation:
                    save = self.query_one("#setup-save", Button)
                    save.disabled = False
                    save.focus(scroll_visible=False)
                    self.reviewed.set()

            def show_summary():
                if self.is_attached and generation == self._choice_generation:
                    self.call_after_refresh(enable_save)

            self.call_after_refresh(show_summary)
        elif key in {"setup-save", "setup-skip"}:
            try:
                selected = {} if key == "setup-skip" else self.selected
                text = await asyncio.to_thread(
                    setup.save, selected, completed=True, adaptive=True, dry_run=self.dry_run
                )
                self.query_one("#setup-status", Static).styles.max_height = 6
                self.query_one("#setup-status", Static).display = True
                self.query_one("#setup-status", Static).update(text)
                self.completed.set()
                if not self.dry_run:
                    if self.stay_open:
                        for button in self.query("Button"):
                            button.disabled = True
                        done = self.query_one("#setup-done", Button)
                        done.disabled = False
                        done.display = True
                        done.focus(scroll_visible=False)
                    else:
                        self.dismiss(text)
            except Exception as exc:  # noqa: BLE001 - UI boundary logs and explicitly presents every failure
                self.query_one("#setup-status", Static).display = True
                self.query_one("#setup-status", Static).update(setup.failure(exc))
                self.completed.set()


class SetupApp(App):
    def __init__(self, *, dry_run=False):
        super().__init__()
        self.dry_run = dry_run

    def on_mount(self):
        self.push_screen(SetupScreen(dry_run=self.dry_run, stay_open=True), self.exit)

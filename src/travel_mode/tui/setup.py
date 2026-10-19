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

from travel_mode import app_policy
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
    #setup-header { height: 1; padding: 0 1; color: $text; }
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
    .setup-group { height: 1; margin: 0; text-style: bold; color: $accent; }
    #setup-controls { height: 3; padding: 0 1; }
    #setup-controls Button { min-width: 0; width: 1fr; margin: 0 1 0 0; }
    #setup-controls Button:focus { text-style: bold underline; }
    #setup-controls Button:disabled {
        background: $surface; color: $text-muted; text-opacity: 1;
    }
    #setup-status { height: auto; max-height: 6; padding: 0 1; overflow-y: auto; }
    """

    def __init__(self, *, dry_run=False):
        super().__init__()
        self.dry_run = dry_run
        self.rows = []
        self.selected = {"work": {}, "away": {}}
        self.column = "work"
        self.ready = asyncio.Event()
        self.completed = asyncio.Event()
        self.reviewed = asyncio.Event()
        self.changed = asyncio.Event()
        self.focus_committed = asyncio.Event()
        self._choice_generation = 0

    def compose(self) -> ComposeResult:
        yield Label(t("setup.title"), id="setup-title")
        yield Label(t("setup.keys", scenario=t("scenario." + self.column)), id="setup-help")
        yield Static(id="setup-header")
        with VerticalScroll(id="setup-body"):
            yield Vertical(id="setup-rows")
        yield Static(t("impl.scanning"), id="setup-status", markup=False)
        with Horizontal(id="setup-controls"):
            yield Button(t("setup.column"), id="setup-column")
            yield Button(t("setup.review"), id="setup-review", variant="primary", disabled=True)
            yield Button(t("setup.skip"), id="setup-skip")
            yield Button(t("setup.confirm_save"), id="setup-save", variant="success", disabled=True)

    async def on_mount(self):
        try:
            self.rows = await asyncio.to_thread(app_policy.discover)
            self.selected = app_policy.choices(self.rows, load_config())
            widgets = []
            group = None
            for index, row in enumerate(self.rows):
                if row["reason"] != group:
                    group = row["reason"]
                    widgets.append(Label(t("setup.group_" + group), classes="setup-group"))
                widgets.append(
                    SetupRow(
                        self.row_label(row),
                        id=f"setup-app-{index}",
                        classes="setup-app",
                        compact=True,
                    )
                )
            await self.query_one("#setup-rows").mount(*widgets)
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
        name_width = max([18, *(cell_len(self.display_name(row)) for row in self.rows)])
        work_width = cell_len(t("scenario.work"))
        away_width = cell_len(t("scenario.away"))
        return width, name_width, work_width, away_width

    @staticmethod
    def display_name(row):
        return t("setup.wechat") if row["name"] == "WeChat" else row["name"]

    def table_line(self, name, work, away, reason):
        width, name_width, work_width, away_width = self.column_widths()
        line = Text(no_wrap=True, overflow="ellipsis")
        for value, size, scenario in (
            (name, name_width, None),
            (work, work_width, "work"),
            (away, away_width, "away"),
        ):
            style = ("bold underline" if scenario == self.column else "dim") if scenario else ""
            line.append(value + " " * max(0, size - cell_len(value)), style)
            line.append(" ")
        line.append(reason)
        return line, width

    def row_label(self, row):
        reason = t("setup.reason_" + row["reason"])
        line, width = self.table_line(
            self.display_name(row),
            t("action." + self.selected["work"][row["name"]]),
            t("action." + self.selected["away"][row["name"]]),
            reason,
        )
        if line.cell_len > width:
            line, _ = self.table_line(
                self.display_name(row),
                t("action." + self.selected["work"][row["name"]]),
                t("action." + self.selected["away"][row["name"]]),
                t("setup.short_" + row["reason"]),
            )
        return line

    def update_table(self):
        self.query_one("#setup-header", Static).update(
            self.table_line(t("setup.app"), t("scenario.work"), t("scenario.away"), t("setup.reason"))[0]
        )
        for index, row in enumerate(self.rows):
            button = self.query_one(f"#setup-app-{index}", SetupRow)
            button.update_label(self.row_label(row))
            button.tooltip = app_policy.row_text(row, self.selected)

    def on_resize(self):
        if self.ready.is_set() and self.rows:
            self.update_table()
            self.call_after_refresh(self.reveal_focused_row)

    def action_move(self, offset):
        if not self.rows:
            return
        focused = (self.focused.id or "") if self.focused else ""
        index = int(focused.removeprefix("setup-app-")) if focused.startswith("setup-app-") else -1
        index = min(len(self.rows) - 1, max(0, index + offset))
        self.query_one(f"#setup-app-{index}", Button).focus()

    def action_edge(self, last):
        if self.rows:
            self.query_one(f"#setup-app-{len(self.rows) - 1 if last else 0}", Button).focus()

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
        if self.focused and self.focused.id and self.focused.id.startswith("setup-app-"):
            self.cycle(int(self.focused.id.removeprefix("setup-app-")))

    async def on_button_pressed(self, event):
        event.stop()
        key = event.button.id
        if key and key.startswith("setup-app-"):
            self.cycle(int(key.removeprefix("setup-app-")))
        elif key == "setup-column":
            self.action_column("away" if self.column == "work" else "work")
        elif key == "setup-review":
            self.reviewed.clear()
            self.query_one("#setup-save", Button).disabled = True
            status = self.query_one("#setup-status", Static)
            status.update(app_policy.summary(self.selected, config_path()))
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
                self.query_one("#setup-status", Static).display = True
                self.query_one("#setup-status", Static).update(text)
                self.completed.set()
                if not self.dry_run:
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
        self.push_screen(SetupScreen(dry_run=self.dry_run), self.exit)

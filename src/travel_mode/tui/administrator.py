"""Permission decisions happen in the interface; passwords stay in the terminal."""

import asyncio
from typing import ClassVar

from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from travel_mode.i18n import t


class AdministratorScreen(ModalScreen):
    BINDINGS: ClassVar = [("escape", "cancel", ""), ("q", "cancel", "")]
    DEFAULT_CSS = """
    AdministratorScreen { align: center middle; background: $background 80%; }
    AdministratorScreen > Vertical {
        width: 90%; max-height: 90%; height: auto; border: round $primary; padding: 1;
    }
    AdministratorScreen VerticalScroll { height: auto; max-height: 5; }
    AdministratorScreen Static { height: auto; }
    AdministratorScreen Button { width: 1fr; }
    """

    def __init__(self, requests):
        super().__init__()
        self.names = list(dict.fromkeys(name for _, name in requests))
        self.mounted = asyncio.Event()

    def compose(self):
        with Vertical():
            yield Static(t("admin.title"), classes="heading")
            yield Static(t("admin.explanation"))
            with VerticalScroll():
                yield Static("\n".join("• " + name for name in self.names), markup=False)
            yield Static(t("admin.instructions"), id="admin-instructions")
            yield Button(t("admin.enter"), id="admin-enter", variant="primary")
            yield Button(t("admin.skip"), id="admin-skip")
            yield Button(t("admin.cancel"), id="admin-cancel")

    def on_mount(self):
        self.query_one("#admin-enter").focus()
        self.mounted.set()

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id.removeprefix("admin-"))

    def action_cancel(self):
        self.dismiss("cancel")

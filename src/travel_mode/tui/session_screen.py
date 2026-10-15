"""Read-only, scrollable session and history reports."""

from typing import ClassVar

from rich.markup import escape
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from travel_mode.i18n import t


class SessionScreen(ModalScreen):
    BINDINGS: ClassVar = [("escape", "dismiss", "")]
    DEFAULT_CSS = """
    SessionScreen { align: center middle; background: $background 80%; }
    SessionScreen > Vertical { width: 90%; height: 85%; border: round $primary; padding: 1; }
    SessionScreen VerticalScroll { height: 1fr; }
    SessionScreen Static { height: auto; }
    """

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self):
        with Vertical():
            with VerticalScroll():
                yield Static(escape(self.text), id="session-text")
            yield Button(t("session.close"), id="session-close")

    def on_button_pressed(self, event):
        if event.button.id == "session-close":
            event.stop()
            self.dismiss()

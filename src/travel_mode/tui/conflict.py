"""One explicit decision before changing a previously opened session."""

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from travel_mode.i18n import t
from travel_mode.session_conflict import describe


class SessionConflict(ModalScreen):
    DEFAULT_CSS = """
    SessionConflict { align: center middle; background: $background 80%; }
    SessionConflict > Vertical { width: 90%; height: auto; border: round $primary; padding: 1; }
    SessionConflict Button { width: 1fr; }
    """

    def __init__(self, state):
        super().__init__()
        self.state = state

    def compose(self):
        with Vertical():
            yield Label(describe(self.state), markup=False)
            yield Button(t("conflict.restore"), id="conflict-restore", variant="primary")
            yield Button(t("conflict.continue"), id="conflict-continue")
            yield Button(t("conflict.cancel"), id="conflict-cancel")

    def on_mount(self):
        self.query_one("#conflict-restore").focus()

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id.removeprefix("conflict-"))

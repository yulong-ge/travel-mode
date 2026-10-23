"""Secondary capabilities are explicit, finite user actions."""

from pathlib import Path
from typing import ClassVar

from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from travel_mode.i18n import t


class UtilityScreen(ModalScreen):
    BINDINGS: ClassVar = [("escape", "close", "")]
    DEFAULT_CSS = """
    UtilityScreen { align: center middle; background: $background 70%; }
    UtilityScreen > Vertical {
        width: 55; max-width: 95%; height: auto; max-height: 95%; border: round $primary; padding: 1;
    }
    UtilityScreen Button { width: 1fr; height: 1; min-height: 1; border: none; background: transparent; }
    """

    def __init__(self, menubar_show="always"):
        super().__init__()
        self.menubar_show = menubar_show

    def compose(self):
        with Vertical():
            yield Static(t("redesign.more"))
            for key, message in (
                ("b-new-plan", "redesign.new_plan"),
                ("b-setup", "setup.adjust"),
                ("b-measure", "redesign.measure"),
                ("b-energy", "redesign.energy"),
                ("b-history", "session.history"),
                ("b-thaw", "redesign.thaw"),
                ("b-category-sync", "categories.pause_sync"),
                ("b-category-updater", "categories.pause_updater"),
                ("b-automation", "redesign.auto"),
            ):
                yield Button(t(message), id=key)
            yield Button(
                t("menubar.mode_setting", mode=t("menubar.mode_" + self.menubar_show)), id="b-menubar"
            )
            yield Button(t("session.close"), id="close-more")

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(None if event.button.id == "close-more" else event.button.id)

    def action_close(self):
        self.dismiss(None)


class ThawScreen(ModalScreen):
    BINDINGS: ClassVar = [("escape", "close", "")]
    DEFAULT_CSS = """
    ThawScreen { align: center middle; background: $background 70%; }
    ThawScreen > Vertical { width: 65; max-width: 95%; height: 80%; border: round $primary; padding: 1; }
    ThawScreen VerticalScroll { height: 1fr; }
    ThawScreen Button { height: 1; min-height: 1; border: none; }
    """

    def __init__(self, paths):
        super().__init__()
        self.paths = paths

    def compose(self):
        with Vertical():
            yield Static(t("redesign.thaw"))
            with VerticalScroll():
                for index, path in enumerate(self.paths):
                    button = Button(Path(path).stem, id=f"resume-{index}")
                    button.app_path = path
                    yield button
            yield Button(t("session.close"), id="close-thaw")

    async def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "close-thaw":
            self.dismiss()
            return
        from travel_mode.tui.widgets import Button as ActionButton

        # Dismiss before admitting a transaction onto the mounted product screen.
        path = event.button.app_path
        app = self.app
        self.dismiss()
        proxy = ActionButton(Path(path).stem, id="thaw-one")
        proxy.app_path, proxy.app_name = path, Path(path).stem
        app.run_worker(app.on_button_pressed(ActionButton.Pressed(proxy)), group="thaw")

    def action_close(self):
        self.dismiss()

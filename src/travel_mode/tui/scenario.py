"""Text cards: number keys focus; Enter confirms without auto-applying."""

from typing import ClassVar

from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from travel_mode.i18n import t


class ScenarioCard(Static, can_focus=True):
    def __init__(self, scenario, number):
        super().__init__(
            f"[bold]{number}  {t('scenario.' + scenario)}[/]\n{t('scenario.' + scenario + '_desc')}",
            id="scenario-" + scenario,
            classes="scenario-card",
        )
        self.scenario = scenario

    def on_focus(self):
        owner = self.screen if isinstance(self.screen, ScenarioPicker) else self.app
        owner.picker_scenario = self.scenario

    async def on_click(self):
        self.focus()

    def on_key(self, event):
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            owner = self.screen if isinstance(self.screen, ScenarioPicker) else self.app
            self.app.run_worker(owner.choose_scenario(self.scenario), group="scenario", exclusive=True)


class ScenarioPicker(ModalScreen):
    """Select another plan without unmounting the active session's controls."""

    BINDINGS: ClassVar = [
        ("1", "focus_scenario('work')", ""),
        ("2", "focus_scenario('away')", ""),
        ("escape", "cancel", ""),
    ]
    DEFAULT_CSS = """
    ScenarioPicker { align: center middle; background: $background 80%; }
    ScenarioPicker > VerticalScroll { width: 95%; height: auto; max-height: 90%; }
    """

    def compose(self):
        with VerticalScroll(id="scenario-picker"):
            yield Static(t("scenario.prompt"), classes="heading")
            yield Static(t("redesign.picker_hint"), classes="muted")
            yield ScenarioCard("work", 1)
            yield ScenarioCard("away", 2)

    def on_mount(self):
        self.action_focus_scenario("work")

    def action_focus_scenario(self, scenario):
        self.query_one("#scenario-" + scenario).focus()

    async def choose_scenario(self, scenario):
        self.dismiss(scenario)

    def action_cancel(self):
        self.dismiss(None)

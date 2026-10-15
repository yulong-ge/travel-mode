"""Top-level page layout."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Label, RichLog, TabPane

from travel_mode.i18n import t
from travel_mode.tui.widgets import Button, TravelTabs


class Layout:
    def compose(self) -> ComposeResult:
        header = Header(show_clock=True)
        header.icon = "○"
        yield header
        if self.scenario == "ask":
            with VerticalScroll(id="scenario-picker"):
                yield Label(t("scenario.prompt"), classes="scenario-title")
                for name in ("work", "away"):
                    with Vertical(classes="scenario-card"):
                        yield Button(t(f"scenario.{name}"), id=f"scenario-{name}", variant="primary")
                        yield Label(t(f"scenario.{name}_desc"))
            yield Footer(show_command_palette=False)
            return
        with TravelTabs(id="body", initial="p-dash"):
            with TabPane(t("impl.overview"), id="p-dash"), VerticalScroll():
                yield from self.compose_dashboard()
            with TabPane(t("impl.power"), id="p-power"), VerticalScroll():
                yield from self.compose_power()
            with TabPane(t("impl.apps_tab"), id="p-apps"), VerticalScroll():
                yield from self.compose_apps()
            with TabPane(t("services.title"), id="p-svc"), VerticalScroll():
                yield from self.compose_services()
            with TabPane(t("impl.network_restore"), id="p-net"), VerticalScroll():
                yield from self.compose_network()
        log = RichLog(id="log", markup=True, max_lines=400, wrap=True)
        log.border_title = t("impl.log")
        yield log
        yield Footer(show_command_palette=False)

"""Services presentation and action selection."""

from __future__ import annotations

from rich.markup import escape
from textual.containers import Horizontal, Vertical
from textual.widgets import Label

from travel_mode.actions.apps import (
    UnloadAgent,
)
from travel_mode.i18n import t
from travel_mode.tui.widgets import Button


class ServicesPage:
    def compose_services(self):
        with Horizontal(classes="cardrow"):
            yield Label(
                t("services.launchd_intro") + " " + t("impl.unloaded_launchagents_are_restored_by_off"),
                classes="grow",
            )
            yield Button(t("impl.unload_all"), id="b-svc-all", variant="primary", classes="btn")
        yield Vertical(Label(t("impl.scanning"), classes="srow"), id="svc-box")

    async def build_svc(self, spot):
        from travel_mode.system.launchd import service_information

        box = self.query_one("#svc-box", Vertical)
        await box.remove_children()
        self.svc_btns = {}
        w = []
        for i, s in enumerate(self.svc["unloadable"]):
            cpu = f", CPU {s['cpu']}%" if s["cpu"] else ""
            lab = Label(f"{escape(s['label'])} [dim]pid {s['pid']}{cpu}[/]", classes="grow")
            b = Button(t("impl.unload"), id=f"svc-{i}", classes="btn")
            w.append(Horizontal(lab, b, classes="row"))
            self.svc_btns[i] = b
        if not self.svc["unloadable"]:
            w.append(Label(t("impl.no_unloadable_agents_are_loaded"), classes="srow"))
        if spot:
            w.append(Button(t("impl.stop_spotlight_indexing"), id="b-svc-spot", classes="scope"))
        else:
            w.append(Label(t("impl.spotlight_indexing_disabled_2"), classes="srow"))
        for k in self.svc["kept"]:
            w.append(Label(t("impl.kept_2", v0=escape(k)), classes="srow"))
        for line in service_information(self.svc):
            w.append(Label(line, classes="srow"))
        from travel_mode.system.launchd import invalid_plist_details

        for detail in invalid_plist_details(self.svc):
            self.logline("dim", detail)
        if self.svc.get("candidates"):
            w.append(Label(t("services.candidates_heading"), classes="hint"))
            for row in self.svc["candidates"]:
                w.append(
                    Label(
                        f"{row['label']} [dim]pid {row['pid']} CPU {row['cpu'] or '?'}%[/]",
                        classes="srow",
                    )
                )
        await box.mount(*w)

    async def act_svc_all(self, btn):
        def flow():
            n = 0
            for s in self.svc["unloadable"]:
                _, rc = self.execute(UnloadAgent(s["plist_path"]))
                if rc == 0:
                    self.save()
                    n += 1
                    self.tlog("ok", t("impl.unloaded", v0=s["label"]))
                else:
                    self.tlog("warn", t("impl.unloading_failed_2", v0=s["label"]))
            self.tlog("ok", t("impl.agents_unloaded", v0=n))

        await self.busy(btn, flow)
        self.action_refresh()

    async def act_svc(self, i, btn):
        s = self.svc["unloadable"][i]
        res = await self.busy(btn, self.execute, UnloadAgent(s["plist_path"]))
        rc = res[1] if isinstance(res, tuple) else 1
        if rc == 0:
            self.save()
            self.logline("ok", t("impl.unloaded_off_restores_it", v0=s["label"]))
            btn.disabled = True
            btn._mutation_done = True
        else:
            self.logline("warn", t("impl.unloading_failed", v0=s["label"]))

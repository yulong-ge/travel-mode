"""Power presentation and action selection."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Checkbox, Label, Static, TabbedContent

from travel_mode import config as config_api
from travel_mode.actions.power import (
    SetBrightness,
    SetPowerProfile,
)
from travel_mode.presentation import action_message as t
from travel_mode.system import brightness, pmset
from travel_mode.tui.widgets import Button


class PowerPage:
    def compose_power(self):
        yield Label(t("impl.battery_profile_select_changes_for_one"), classes="hint")
        yield Vertical(Label(t("impl.scanning"), classes="srow"), id="power-list")
        control = Vertical(id="hib25-control")
        control.styles.height = "auto"
        yield control
        yield Checkbox(t("impl.disable_powernap_womp_on_ac_too"), id="cb-ac", classes="scope")
        from travel_mode.system.brightness import available as brightness_available

        if brightness_available():
            yield Checkbox(
                t("impl.brightness_to_off_restores_the_original"),
                self.profile_values.get("brightness") is not None,
                id="cb-bright",
                classes="scope",
            )
        else:
            yield Label(t("impl.brightness_not_installed_brew_install_brightness"), classes="hint")
        yield Button(t("impl.apply_selected"), id="b-power", variant="primary", classes="scope")

    async def build_power(self, profs, supported):
        supported = supported & profs.get("b", {}).keys()
        self.hib25_supported = "hibernatemode" in supported and "hibernatemode" in profs.get("b", {})
        ac_control = self.main_query_one("#cb-ac", Checkbox)
        ac_control.disabled = not all(key in profs.get("a", {}) for key in ("powernap", "womp"))
        if ac_control.disabled:
            ac_control.value = False
        ac_control.label = t(
            "power.ac_unavailable" if ac_control.disabled else "impl.disable_powernap_womp_on_ac_too"
        )
        hib = self.main_query_one("#hib25-control")
        if self.hib25_supported and not list(self.main_query("#cb-hib25")):
            await hib.mount(
                Checkbox(
                    t("impl.hibernatemode_for_extended_sleep"),
                    self.profile_values.get("power", {}).get("battery", {}).get("hibernatemode") == 25,
                    id="cb-hib25",
                    classes="scope",
                )
            )
        elif not self.hib25_supported:
            await hib.remove_children()
        box = self.main_query_one("#power-list", Vertical)
        await box.remove_children()
        self.pw_boxes = []
        w = []

        w.append(Label(t("power.profile_intro"), classes="hint"))
        self.power_view = pmset.power_preview(
            profs, scenario=self.scenario, supported=supported, config=config_api.load_config()
        )
        for row in self.power_view["changes"]:
            key = row["key"]
            title = f"{row['name']}: {row['current']} → {row['target']}"
            heading = Checkbox(title, True, id=f"pw-{'lowpowermode' if key == 'powermode' else key}")
            self.pw_boxes.append((key, row["target"], heading))
            w.append(
                Vertical(heading, Static(row["explanation"], classes="power-detail"), classes="power-item")
            )
        if self.power_view["satisfied"]:
            w.append(
                Label(
                    t("preview.satisfied", names=", ".join(self.power_view["satisfied"])),
                    classes="power-title",
                )
            )
        if self.power_view["unavailable"]:
            w.append(
                Label(
                    t("preview.unavailable", names=", ".join(self.power_view["unavailable"])), classes="hint"
                )
            )
        for row in self.power_view["ac_changes"]:
            w.append(Static(row["explanation"], classes="power-detail"))
        await box.mount(*w)
        self._update_power_btn()

    def _update_power_btn(self):
        n = sum(1 for _, _, cb in self.pw_boxes if cb.value)
        if any(cb.value for cb in self.main_query("#cb-hib25")):
            n += 1
        self.main_query_one("#b-power", Button).label = t("impl.apply_selected_2", v0=n)

    def on_checkbox_changed(self, ev):
        if ev.checkbox.id and (ev.checkbox.id.startswith("pw-") or ev.checkbox.id in ("cb-hib25",)):
            self._update_power_btn()
        # 焦点立即离开 pane 内控件: 焦点滞留隐藏 pane 会把 active 弹回旧页 (TabPane.Focused)
        self.main_query_one(TabbedContent).focus()

    async def act_power(self):
        chg = []
        for key, rec, cb in self.pw_boxes:
            if cb.value:
                chg += [key, rec]
        if self.hib25_supported and any(cb.value for cb in self.main_query("#cb-hib25")):
            for i in range(0, len(chg), 2):
                if chg[i] == "hibernatemode":
                    chg[i + 1] = "25"
                    break
            else:
                chg += ["hibernatemode", "25"]
        ac_control = self.main_query_one("#cb-ac", Checkbox)
        ac_wanted = ac_control.value and not ac_control.disabled
        bright_wanted = any(cb.value for cb in self.main_query("#cb-bright"))
        if not chg and not ac_wanted and not bright_wanted:
            self.notify(t("impl.no_power_settings_selected"), severity="warning")
            return
        if chg:
            _, rc = await self.in_thread(self.execute, SetPowerProfile([*chg]))
            if rc == 0:
                self.save()  # 电源变更必须落盘, 否则 off 无法回滚且会污染下次锚点
                for i in range(0, len(chg), 2):
                    self.logline("ok", f"{chg[i]} → {chg[i + 1]}")
            else:
                self.logline("warn", t("impl.applying_pmset_failed_2", v0=" ".join(chg)))
        if ac_wanted:
            _, rc2 = await self.in_thread(self.execute, SetPowerProfile(["powernap", "0", "womp", "0"], "-c"))
            if rc2 == 0:
                self.save()
            self.logline(
                "ok" if rc2 == 0 else "warn",
                t("impl.disabled_powernap_womp_on_ac") if rc2 == 0 else t("impl.changing_ac_settings_failed"),
            )
        if bright_wanted:
            orig = await self.in_thread(brightness.current_brightness)
            _, rcb = await self.in_thread(
                self.execute, SetBrightness(self.profile_values.get("brightness", 0.15))
            )
            if rcb == 0:
                # 只记录首次原始值 (同 run_restore 语义, 见文本模式同段注释)
                self.save()
                self.logline("ok", t("impl.brightness_was", v0=orig or "?"))
            else:
                self.logline("warn", t("impl.changing_brightness_failed"))
        self.action_refresh()

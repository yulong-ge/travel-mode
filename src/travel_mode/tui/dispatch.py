"""Dispatch presentation and action selection."""

from __future__ import annotations

import asyncio

from textual.widgets import TabbedContent

from travel_mode.actions.apps import (
    QuitConfiguredDaemons,
)
from travel_mode.actions.network import BluetoothOff, WifiOff
from travel_mode.actions.power import (
    EnableSleep,
    KillCaffeinate,
    StopSpotlight,
)
from travel_mode.presentation import action_message as t
from travel_mode.system import network as system_network
from travel_mode.system import spotlight
from travel_mode.system.shell import is_dry_run
from travel_mode.tui.widgets import Button


class Dispatch:
    async def on_button_pressed(self, ev):
        completion = getattr(ev, "travel_completion", None)
        if completion is None:
            completion = self.track_input()
        try:
            if not self._interface_closing:
                await self._handle_button_pressed(ev)
        except Exception as exc:
            import os

            from travel_mode.failures import BatchStopped

            if not isinstance(exc, BatchStopped):
                self.report_action_error(exc)
            if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
                raise
        finally:
            self.complete_input(completion)

    async def _handle_button_pressed(self, ev):
        from travel_mode.actions.journal import mutation_scope
        from travel_mode.state import StateError, _path

        if ev.button.id in {"scenario-work", "scenario-away"}:
            await self.choose_scenario(ev.button.id.removeprefix("scenario-"))
            return
        if ev.button.id == "b-history":
            await self.show_history()
            return

        if not self.origin_ready:
            if ev.button.id == "b-doctor" and self._origin_error is not None:
                self.action_refresh()
                return
            if self._origin_error is not None:
                self.notify(t("health.origin_failed", reason=self._origin_error), severity="error")
                return
            self.notify(t("scenario.preparing"), severity="warning")
            return
        with self.scenario_scope():
            if ev.button.id == "b-energy":
                self.refresh_audit_detail()
                return
            if ev.button.id == "b-doctor":
                if not self._mutation_lock.locked():
                    self.action_refresh()
                return
            if ev.button.id in {"b-dash-apps", "b-dash-svc"}:
                await self._dispatch_button(ev)
                return
            if self._mutation_lock.locked() or ev.button.disabled:
                return
            if getattr(ev, "travel_generation", self._mutation_generation) != self._mutation_generation:
                return
            loop = asyncio.get_running_loop()

            async def acquire():
                from travel_mode.system.sudo import SudoKeepAlive

                self.logline("dim", t("impl.sudo_required_enter_your_password_in"))
                with self.suspend():
                    keepalive = await self.in_thread(SudoKeepAlive().__enter__)
                self.logline("ok", t("impl.sudo_credentials_acquired"))
                return keepalive

            def authenticate():
                return asyncio.run_coroutine_threadsafe(acquire(), loop).result()

            async with self.executor.transaction(
                self._mutation_lock, self.st, self.merged, self.save, authenticate
            ):
                if self.merged and not _path().exists():
                    raise StateError(t("impl.snapshot_deleted_externally_possibly_by_off"))
                from travel_mode.state import state_snapshot

                self._transaction_view = state_snapshot(self.st)
                self._mutation_action = str(ev.button.label)
                self.sub_title = t("impl.executing_action", action=self._mutation_action)
                self.logline("dim", self.sub_title)
                for button in self.query(Button):
                    self._disable_mutation_button(button)
                from travel_mode.failures import batch

                try:
                    with batch(self.logline):
                        if ev.button.id == "b-off":
                            await self._dispatch_button(ev)
                        else:
                            with mutation_scope(self.st, self.save):
                                await self._dispatch_button(ev)
                finally:
                    try:
                        self.flush_session_observations()
                    finally:
                        self._mutation_action = None
                        self._transaction_view = None
                        self._mutation_generation += 1
                        for button, disabled in self._disabled_buttons.items():
                            if button.is_attached:
                                button.disabled = disabled or button._mutation_done
                        self._disabled_buttons.clear()
                        self.sub_title = (
                            t("impl.travel_mode_dry_run")
                            if self._dry_run or is_dry_run()
                            else t("impl.travel_mode")
                        )

    async def _dispatch_button(self, ev):
        bid = ev.button.id or ""
        # 焦点立即离开 pane 内控件: 焦点滞留隐藏 pane 会把 active 弹回旧页 (TabPane.Focused)
        self.query_one(TabbedContent).focus()
        if bid == "b-quick":
            await self.act_quick(ev.button)
        elif bid.startswith("b-health-"):
            await self.act_health(ev.button)
        elif bid == "b-reapply":
            from travel_mode.resurrection import reapply

            await self.busy(ev.button, reapply, self.st, self.execute, self.save)
            self.action_refresh()
        elif bid == "b-fixsleep":
            _, rc = await self.in_thread(self.execute, EnableSleep())
            self.logline("ok" if rc == 0 else "warn", "disablesleep → 0" if rc == 0 else t("impl.fix_failed"))
            if rc == 0:
                self.save()
            self.action_refresh()
        elif bid.startswith("b-killcaff-"):
            target = self.caffeinate_targets[int(bid.removeprefix("b-killcaff-"))]
            _, rc = await self.in_thread(self.execute, KillCaffeinate(target))
            self.logline(
                "ok" if rc == 0 else "bad",
                t("impl.caffeinate_terminated") if rc == 0 else t("failure.incomplete"),
            )
            self.action_refresh()
        elif bid == "b-quituu":
            _, okq, msg = await self.in_thread(self.execute, QuitConfiguredDaemons(self.st, self.save))
            self.logline("ok" if okq else "warn", f"daemon app: {msg}")
            self.action_refresh()
        elif bid == "b-dash-apps":
            self.action_goto("p-apps")
        elif bid == "b-dash-svc":
            self.action_goto("p-svc")
        elif bid == "b-power":
            await self.act_power()
        elif bid == "b-apps-all":
            await self.act_apps_all(ev.button)
        elif bid in {"b-category-sync", "b-category-updater"}:
            categories = {"sync", "backup"} if bid == "b-category-sync" else {"updater"}
            await self.act_category(categories, ev.button)
        elif bid == "b-svc-all":
            await self.act_svc_all(ev.button)
        elif bid.startswith("btn-"):
            _, i, a = bid.split("-")
            await self.act_app(int(i), a, ev.button)
        elif bid.startswith("svc-"):
            await self.act_svc(int(bid[4:]), ev.button)
        elif bid in ("b-spot", "b-svc-spot"):
            if not await self.in_thread(spotlight.spotlight_on):
                self.logline("dim", t("impl.spotlight_indexing_already_disabled_no_action"))
                ev.button.disabled = True
                ev.button._mutation_done = True
                return
            _, rc = await self.in_thread(self.execute, StopSpotlight())
            if rc == 0:
                self.save()
                self.logline("ok", t("impl.spotlight_indexing_disabled_off_restores_it"))
                ev.button.disabled = True
                ev.button._mutation_done = True
            else:
                self.logline("warn", t("impl.mdutil_failed"))
        elif bid.startswith("netretry-"):
            index = int(bid.removeprefix("netretry-"))
            self.net_failures.pop(self.net_paths[index][1], None)
            await self.act_net(index, ev.button)
        elif bid.startswith("net-"):
            await self.act_net(int(bid[4:]), ev.button)
        elif bid == "b-wifi":
            dev = system_network.wifi_dev()
            if dev and not await self.in_thread(system_network.wifi_powered, dev):
                self.logline("dim", t("impl.wi_fi_already_disabled_no_action"))
                ev.button.disabled = True
                ev.button._mutation_done = True
            elif dev:
                _, rc = await self.in_thread(self.execute, WifiOff(dev))
                if rc == 0:
                    self.save()
                    self.logline("ok", t("impl.wi_fi_disabled_off_restores_it", v0=dev))
                    ev.button.disabled = True
                    ev.button._mutation_done = True
                else:
                    self.logline("warn", t("impl.disabling_wi_fi_failed"))
        elif bid == "b-bt":
            if not await self.in_thread(system_network.bt_powered):
                self.logline("dim", t("impl.bluetooth_already_disabled_no_action_needed"))
                ev.button.disabled = True
                ev.button._mutation_done = True
            else:
                _, rc = await self.in_thread(self.execute, BluetoothOff())
                if rc == 0:
                    self.save()
                    self.logline("ok", t("impl.bluetooth_disabled_off_restores_it"))
                    ev.button.disabled = True
                    ev.button._mutation_done = True
                else:
                    self.logline("warn", t("impl.blueutil_failed"))
        elif bid == "b-off":
            await self.act_off(ev.button)

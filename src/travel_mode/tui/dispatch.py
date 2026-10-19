"""Dispatch presentation and action selection."""

from __future__ import annotations

import asyncio

from travel_mode.presentation import action_message as t
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
        if ev.button.id == "b-setup":
            self.open_setup()
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
            if self._mutation_lock.locked() or ev.button.disabled:
                return
            if getattr(ev, "travel_generation", self._mutation_generation) != self._mutation_generation:
                return
            from travel_mode.snapshots import load_state

            disk = await self.in_thread(load_state)
            if disk and (
                not self.merged or disk.get("session", {}).get("id") != self.st.get("session", {}).get("id")
            ):
                self.st, self.merged, self._session_admitted = disk, True, False
            if self.merged and not _path().exists():
                raise StateError(t("impl.snapshot_deleted_externally_possibly_by_off"))
            if (
                self.merged
                and not self._session_admitted
                and self.view_mode != "session"
                and ev.button.id not in {"b-off", "b-reapply"}
            ):
                from travel_mode.tui.conflict import SessionConflict

                def answered(choice):
                    if choice != "cancel":
                        self.run_worker(self.admit_session(choice, ev), group="session-decision")
                    else:
                        self.session_decision_complete.set()

                self.session_decision_complete.clear()
                self.push_screen(SessionConflict(self.st), answered)
                return
            async with self.executor.transaction(
                self._mutation_lock, self.st, self.merged, self.save, self.action_authenticator()
            ):
                if self.merged and not _path().exists():
                    raise StateError(t("impl.snapshot_deleted_externally_possibly_by_off"))
                from travel_mode.state import state_snapshot

                self._transaction_view = state_snapshot(self.st)
                self._mutation_action = str(ev.button.label)
                self.sub_title = t("impl.executing_action", action=self._mutation_action)
                self.logline("dim", self.sub_title)
                for button in self.main_query(Button):
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
            if getattr(self, "_refresh_after_action", False):
                self._refresh_after_action = False
                self.action_refresh()

    def action_authenticator(self):
        loop = asyncio.get_running_loop()

        async def acquire():
            from travel_mode.system.sudo import SudoKeepAlive

            self.logline("dim", t("impl.sudo_required_enter_your_password_in"))
            with self.suspend():
                keepalive = await self.in_thread(SudoKeepAlive().__enter__)
            self.logline("ok", t("impl.sudo_credentials_acquired"))
            return keepalive

        return lambda: asyncio.run_coroutine_threadsafe(acquire(), loop).result()

    async def admit_session(self, choice, event):
        from travel_mode import snapshots
        from travel_mode.session_conflict import resolve, restore_active

        try:
            if choice == "restore":
                async with self.executor.transaction(
                    self._mutation_lock, self.st, self.merged, self.save, self.action_authenticator()
                ):
                    if not await self.in_thread(restore_active, self.st, self.tlog):
                        return
                    with self.scenario_scope():
                        self.st, self.merged = await self.in_thread(snapshots.snapshot_origin)
                    self._app_outcomes = {}
            else:
                resolve(self.st, self.scenario, choice=choice, restore=lambda: False)
            self._session_admitted = True
            self.logline("ok", t("conflict.ready"))
            await self._handle_button_pressed(event)
        finally:
            self.session_decision_complete.set()

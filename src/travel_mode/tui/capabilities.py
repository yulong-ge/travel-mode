"""Secondary app preferences and restore reports, using shared flows."""

from travel_mode import snapshots
from travel_mode.flows import restore as flow_restore
from travel_mode.i18n import t


class Capabilities:
    def offer_remember(self, name, action, default):
        from travel_mode.flows import setup
        from travel_mode.tui.setup import ConfirmRemember

        if self._dry_run or not self.remember.offer(name, self.scenario, action, default):
            return
        scenario = self.scenario

        def answered(accepted):
            try:
                text = self.remember.answer(name, scenario, action, accepted)
                if text:
                    self.logline("ok", text)
                    from travel_mode.config import load_config
                    from travel_mode.paths import config_path

                    self.config = load_config(config_path())
            except Exception as exc:  # noqa: BLE001 - UI boundary logs and explicitly presents every failure
                self.logline("bad", setup.failure(exc))

        self.push_screen(ConfirmRemember(self.remember.prompt(name, scenario, action)), answered)

    async def act_off(self, btn, only=None):
        age = snapshots.anchor_age_hours(self.st)
        if age is not None and age > 24:
            self.logline("warn", t("impl.snapshot_is_hours_old_restoring_the", v0=age))

        def flow():
            from travel_mode import sessions

            def emit(kind, message):
                self.tlog(kind, message)

            n, fails = (
                flow_restore.run_restore(self.st, emit)
                if only is None
                else flow_restore.run_restore(self.st, emit, only=only)
            )
            remaining = only is not None and any(
                self.st.get(key)
                for key in (
                    "batt_prof",
                    "ac_prof",
                    "sleepdisabled",
                    "spotlight_off",
                    "wifi_off",
                    "bt_off",
                    "brightness",
                    "unloaded_sys_svcs",
                    "unloaded_svcs",
                    "frozen_apps",
                    "frozen_pids",
                    "quit_apps",
                )
            )
            if fails or remaining:
                if "session" in self.st:
                    self.st["session"]["restored"] += n
                    self.st["session"]["restore_failures"] += fails
                    self.save()
                return n, fails, None
            report = sessions.finish(self.st, n, 0)
            from travel_mode.state import _path

            _path().unlink(missing_ok=True)
            return n, 0, report

        res = await self.busy(btn, flow)
        if not res:
            self.action_refresh()
            return
        n, fails, report = res
        if report is not None:
            from travel_mode import sessions
            from travel_mode.tui.session_screen import SessionScreen

            await self.push_screen(
                SessionScreen("\n".join(sessions.describe(report, sessions.read_history())))
            )
        if fails or report is None:
            from travel_mode.actions.privilege_plan import pending_restore

            rows = pending_restore(self.st)
            names = t("admin.separator").join(
                t("admin.pending_item", name=row["name"], reason=row["reason"]) for row in rows
            )
            text = t("admin.restore_pending", n=fails or len(rows), names=names)
            self.logline("bad", text)
            self.notify(text, severity="warning")
        else:
            self.logline("ok", t("impl.restore_complete_items_state_file_cleared", v0=n))
            self.notify(t("impl.original_state_restored_items", v0=n), severity="information")
        self.st, self.merged = snapshots.snapshot_origin()
        self._session_admitted = True
        self._app_outcomes = {}
        self.action_refresh()

    async def show_history(self):

        from travel_mode import sessions

        rows = await self.in_thread(sessions.read_history)
        text = "\n\n".join("\n".join(sessions.describe(row, rows)) for row in reversed(rows))
        from travel_mode.tui.session_screen import SessionScreen

        await self.push_screen(SessionScreen(text or t("session.history_empty")))

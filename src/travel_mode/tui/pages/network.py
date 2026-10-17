"""Network presentation and action selection."""

from __future__ import annotations

from rich.markup import escape
from textual.containers import ItemGrid, Vertical, VerticalScroll
from textual.widgets import Label, Static

from travel_mode import snapshots
from travel_mode.actions.apps import (
    ForceQuitApp,
    QuitApp,
    QuitDaemonApp,
    configured_daemon,
)
from travel_mode.flows import restore as flow_restore
from travel_mode.presentation import action_message as t
from travel_mode.tui.widgets import Button


class NetworkPage:
    def compose_network(self):
        with VerticalScroll(id="net-scroll"):
            net = Vertical(classes="card")
            net.border_title = t("impl.disconnect_kept_by_default")
            with net:
                yield Vertical(Label(t("impl.scanning"), classes="srow"), id="net-box")
        actions = Vertical(classes="card")
        actions.border_title = t("impl.network_actions")
        with actions:
            yield Vertical(id="net-actions")
        off = Vertical(classes="card")
        off.border_title = t("impl.restore_original_state")
        with off:
            yield Static("", id="off-info")
            yield Button(t("impl.restore_travel_mode_off"), id="b-off", variant="error")
            yield Button(t("session.history"), id="b-history")

    async def build_net(self, network, dev, wifi, bluetooth):
        box = self.query_one("#net-box", Vertical)
        await box.remove_children()
        actions = self.query_one("#net-actions", Vertical)
        await actions.remove_children()
        self.net_paths = {}
        self.net_failures = {
            r["target"]: r
            for r in self.display_state().get("action_failures", [])
            if not r.get("recovered") and r["command"][:1] == ["osascript"]
        }
        w = []
        bottom = []
        idx = 0
        for row in network:
            label = t("network.status." + row["status"])
            if row["cpu"] is not None:
                label += f" · CPU {row['cpu']:.1f}%"
            if row["resident"]:
                label += " · " + t("network.resident")
            if row["running"] and row["path"]:
                extra = (
                    [Button(t("failure.retry_quit"), id=f"netretry-{idx}", classes="btn")]
                    if row["path"] in self.net_failures
                    else []
                )
                w.append(
                    ItemGrid(
                        Label(f"{escape(row['name'])}: {label}", classes="grow"),
                        Button(
                            t("impl.force_quit_3") if row["path"] in self.net_failures else t("impl.quit_2"),
                            id=f"net-{idx}",
                            classes="btn",
                            variant="error" if row["path"] in self.net_failures else "default",
                        ),
                        *extra,
                        classes="row",
                    )
                )
                self.net_paths[idx] = (row["name"], row["path"])
                idx += 1
            else:
                w.append(Label(f"[dim]{escape(row['name'])}: {escape(label)}[/]", classes="srow"))
        if dev:
            if wifi:
                bottom.append(Button(t("impl.disable_wi_fi", v0=dev), id="b-wifi", classes="scope"))
            else:
                bottom.append(Label(t("impl.wi_fi_already_disabled"), classes="srow"))
        if bluetooth is not None:
            if bluetooth:
                bottom.append(Button(t("impl.disable_bluetooth"), id="b-bt", classes="scope"))
            else:
                bottom.append(Label(t("impl.bluetooth_already_disabled"), classes="srow"))
        else:
            bottom.append(Label(t("impl.blueutil_not_installed_brew_install_blueutil"), classes="srow"))
        bottom.append(Label(t("network.services_hint", tab=t("services.title")), classes="srow"))
        await box.mount(*w)
        await actions.mount(*bottom)
        info = self.query_one("#off-info", Static)
        st = self.display_state()
        if st.get("saved_at"):
            info.update(
                t(
                    "impl.snapshot_apps_frozen_agents_to_restore",
                    v0=st["saved_at"],
                    v1=len(st["quit_apps"]),
                    v2=len(st["frozen_apps"]),
                    v3=len(st["unloaded_svcs"]),
                )
            )
        else:
            info.update(t("impl.no_snapshot_no_changes_made"))

    async def act_net(self, i, btn):
        name, path = self.net_paths[i]
        spec = configured_daemon(name)
        if spec is not None:
            # root daemon 会被 launchd 拉活且持防睡断言, grace_quit 是假成功
            res = await self.busy(btn, self.execute, QuitDaemonApp(spec, self.st, self.save))
            if not res:
                return
            _, okq, msg = res
            if msg:
                self.logline("ok" if okq else "warn", f"{name}: {msg}")
            if okq:
                btn.disabled = True
                btn._mutation_done = True
            return
        operation = (
            ForceQuitApp(path)
            if path in getattr(self, "net_failures", {})
            else QuitApp(path, name, fallback=self.scenario != "work")
        )
        okq = await self.busy(btn, self.execute, operation)
        if okq:
            self.save()
            if not getattr(okq, "forced", False):
                self.logline("ok", t("impl.quit_off_reopens_it_2", v0=name))
            btn.disabled = True
            btn._mutation_done = True
        else:
            self.logline(
                "bad",
                okq.explanation(name) if hasattr(okq, "explanation") else t("impl.did_not_quit", v0=name),
            )
            btn.variant = "error"
            btn.label = t("impl.force_quit_3")
            self.net_failures[path] = True
            self.action_refresh()

    async def act_off(self, btn):
        age = snapshots.anchor_age_hours(self.st)
        if age is not None and age > 24:
            self.logline("warn", t("impl.snapshot_is_hours_old_restoring_the", v0=age))

        def flow():
            from travel_mode import sessions

            n, fails = flow_restore.run_restore(self.st, lambda k, m: self.tlog(k, m))
            if fails:
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
        if fails:
            self.logline("bad", t("impl.restores_failed_snapshot_retained_resolve_failures", v0=fails))
            self.notify(t("impl.restores_failed_snapshot_retained", v0=fails), severity="error")
        else:
            self.logline("ok", t("impl.restore_complete_items_state_file_cleared", v0=n))
            self.notify(t("impl.original_state_restored_items", v0=n), severity="information")
        self.st, self.merged = snapshots.snapshot_origin()
        self.action_refresh()

    async def show_history(self):

        from travel_mode import sessions

        rows = await self.in_thread(sessions.read_history)
        text = "\n\n".join("\n".join(sessions.describe(row, rows)) for row in reversed(rows))
        from travel_mode.tui.session_screen import SessionScreen

        await self.push_screen(SessionScreen(text or t("session.history_empty")))

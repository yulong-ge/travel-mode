"""Apps presentation and action selection."""

from __future__ import annotations

import asyncio

from rich.markup import escape
from textual.containers import ItemGrid, Vertical
from textual.widgets import Label

from travel_mode.actions.apps import (
    ForceQuitApp,
    FreezeApp,
    QuitApp,
    QuitDaemonApp,
    ThawApp,
    configured_daemon,
)
from travel_mode.categories import grouped
from travel_mode.presentation import action_message as t
from travel_mode.system import apps as system_apps
from travel_mode.tui.widgets import Button, _ellip


class AppsPage:
    def compose_apps(self):
        with ItemGrid(classes="cardrow"):
            yield Label(t("impl.default_actions_come_from_config_toml"), classes="grow")
            yield Button(t("impl.apply_all_defaults"), id="b-apps-all", variant="primary", classes="btn")
        yield Vertical(Label(t("impl.scanning"), classes="srow"), id="apps-box")

    async def build_apps(self):
        st = self.display_state()
        box = self.query_one("#apps-box", Vertical)
        await box.remove_children()
        self.app_rows = {}
        w = []
        order = {"quit": 0, "ask": 1, "freeze": 2, "skip": 3}
        running = sorted(self.scan["running"], key=lambda a: order.get(a["defact"], 9))
        for msg in self.scan["nobundle"]:
            w.append(Label(t("impl.skipped_2", v0=escape(msg)), classes="srow"))
        if not running:
            w.append(Label(t("impl.no_listed_high_energy_apps_are_2"), classes="srow"))
        category = None
        for i, app in enumerate(grouped(running)):
            group = app.get("category", "other")
            if group != category:
                w.append(Label(t(f"categories.{group}"), classes="hint"))
                category = group
            name, dfl = app["name"], app["defact"]
            frozen = app["path"] in st.get("frozen_apps", [])
            state_s = t("impl.frozen") if frozen else ""
            lab = Label(
                t(
                    "impl.cpu_default",
                    v0=escape(name),
                    v1=app["cpu"] or "?",
                    v2=t("action." + dfl),
                    v3=state_s,
                ),
                classes="grow",
            )
            if dfl == "skip":
                reason = t("categories.notice") if app.get("category_action") == "notice" else t("impl.skip")
                w.append(Label(f"○ {escape(name)} [dim]({reason})[/]", classes="srow"))
                continue
            bq = Button(
                t("impl.quit_2"),
                id=f"btn-{i}-q",
                variant="primary" if dfl in ("quit", "ask") else "default",
                classes="btn",
            )
            bf = Button(
                t("impl.thaw") if frozen else t("impl.freeze"),
                id=f"btn-{i}-f",
                variant="primary" if dfl == "freeze" else "default",
                classes="btn",
            )
            bs = Button(t("impl.skip"), id=f"btn-{i}-s", classes="btn")
            w.append(ItemGrid(lab, bq, bf, bs, classes="row"))
            self.app_rows[i] = {
                "defact": dfl,
                "name": name,
                "path": app["path"],
                "label": lab,
                "btns": (bq, bf, bs),
                "done": False,
                "kill": False,
                "frozen": frozen,
            }
        for notice in grouped(self.scan.get("notices", [])):
            w.append(
                Label(
                    f"{t('categories.' + notice['category'])}: {notice['name']} — " + t("categories.notice"),
                    classes="srow",
                )
            )
        await box.mount(*w)

    def mark_row_done(self, i, text, style="green", expected_path=None):
        row = self.app_rows.get(i)
        if row is None or (expected_path is not None and row["path"] != expected_path):
            return
        row["done"] = True
        for b in row["btns"]:
            b.disabled = True
            b._mutation_done = True
        row["label"].update(f"[{style}]{text}[/]")

    async def act_apps_all(self, btn):
        rows = tuple((i, dict(row)) for i, row in self.app_rows.items())

        def flow():
            nq = nf = 0
            ask_pending = []
            for i, row in rows:
                if row["done"]:
                    continue
                name, path = row["name"], row["path"]
                act = row["defact"]
                if act == "skip":
                    continue
                if act == "ask":
                    # ask = "问我" — 批量流程无人应答, 跳过 (绝不强杀)
                    ask_pending.append(name)
                    self.tlog("dim", t("impl.default_action_for_is_ask_skipped_2", v0=name))
                    continue
                if act == "quit":
                    spec = configured_daemon(name)
                    if spec is not None:
                        _, okq, msg = self.execute(QuitDaemonApp(spec, self.st, self.save))
                        self.tlog("ok" if okq else "warn", msg)
                        if okq:
                            nq += 1
                            self.call_from_thread(
                                self.mark_row_done, i, t("impl.quit_3", v0=name), expected_path=path
                            )
                        continue
                    if self.execute(QuitApp(path, name)) or (
                        self.scenario != "work" and self.execute(ForceQuitApp(path))
                    ):
                        self.save()
                        nq += 1
                        self.call_from_thread(
                            self.mark_row_done, i, t("impl.quit_3", v0=name), expected_path=path
                        )
                        self.tlog("ok", t("impl.quit", v0=name))
                    else:
                        self.tlog("warn", t("impl.did_not_quit", v0=name))
                        if self.scenario == "work":
                            raise RuntimeError(t("impl.did_not_quit", v0=name))
                elif act == "freeze":
                    for _line in system_apps.app_sleep_assertions(name)[:1]:
                        self.tlog("warn", t("impl.holds_a_sleep_assertion_freezing_will_2", v0=name))
                    if self.execute(FreezeApp(path)):
                        self.save()
                        nf += 1
                        self.call_from_thread(
                            self.mark_row_done, i, t("impl.frozen_3", v0=name), expected_path=path
                        )
                        self.tlog("ok", t("impl.frozen_2", v0=name))
                    else:
                        self.tlog("warn", t("impl.freezing_failed", v0=name))
            msg = t(
                "impl.batch_complete_quit_frozen",
                v0=nq,
                v1=nf,
                v2=t("impl.ask_requires_manual_action", v0=", ".join(ask_pending)) if ask_pending else "",
            )
            self.tlog("ok", msg)

        await self.busy(btn, flow)
        self.action_refresh()

    async def act_app(self, i, action, btn):
        row = self.app_rows.get(i)
        if not row or row["done"]:
            return
        name, path = row["name"], row["path"]
        if action == "s":
            self.mark_row_done(i, t("impl.skipped_3", v0=name), "dim")
            return
        spec = configured_daemon(name)
        if action == "q" and spec is not None:
            _, okq, msg = await self.busy(btn, self.execute, QuitDaemonApp(spec, self.st, self.save))
            self.logline("ok" if okq else "warn", msg)
            if okq:
                self.mark_row_done(i, t("impl.quit_off_reopens_it", v0=name), expected_path=path)
            return
        if action == "q" and row["kill"]:
            okk = await self.busy(btn, self.execute, ForceQuitApp(path))
            if okk:
                self.save()
                self.mark_row_done(i, t("impl.force_quit_2", v0=name), "red", expected_path=path)
                self.logline("ok", t("impl.force_quit", v0=name))
            else:
                self.logline("bad", t("impl.force_quitting_failed", v0=name))
            return
        if action == "q":
            okq = await self.busy(btn, self.execute, QuitApp(path, name))
            if okq:
                self.save()
                self.mark_row_done(i, t("impl.quit_off_reopens_it", v0=name), expected_path=path)
                self.logline("ok", t("impl.quit_off_reopens_it_2", v0=name))
            else:
                row["kill"] = True
                btn.label = t("impl.force_quit_3")
                btn.variant = "error"
                self.logline("warn", t("impl.has_not_quit_possible_unsaved_dialog_2", v0=name))
        elif action == "f":
            if row.get("frozen"):
                # 已冻结 → 按钮变为解冻: CONT 并移出恢复列表 (用户明确表示这次要带着它)

                if not await asyncio.to_thread(self.execute, ThawApp(path, self.st)):
                    raise RuntimeError(t("impl.thaw_failed", v0=path))
                if path in self.st.get("frozen_apps", []):
                    self.save()
                self.mark_row_done(
                    i, t("impl.thawed_off_will_no_longer_restore", v0=name), "cyan", expected_path=path
                )
                self.logline("ok", t("impl.thawed_3", v0=name))
                return
            for line in (await asyncio.to_thread(system_apps.app_sleep_assertions, name))[:1]:
                self.logline(
                    "warn", t("impl.holds_a_sleep_assertion_freezing_will", v0=name, v1=_ellip(line, 70))
                )
            okf = await self.busy(btn, self.execute, FreezeApp(path))
            if okf:
                n = await asyncio.to_thread(system_apps.freeze_result_count, path)
                self.save()
                self.mark_row_done(
                    i, t("impl.frozen_processes_off_resumes_them", v0=name, v1=n), expected_path=path
                )
                self.logline("ok", t("impl.frozen_processes_stopped_off_resumes_them", v0=name, v1=n))
            else:
                self.logline("warn", t("impl.freezing_failed", v0=name))

"""Real tmux TUI debug matrix. Every child has isolated HOME/XDG/config/state."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from harness_evidence import read_layout, write_json, write_layout, write_text
from terminal_render import render, text_width
from textual.binding import Binding

import travel_mode.config as config_api
from travel_mode.i18n import t
from travel_mode.system import apps, launchd
from travel_mode.tui.app import TravelApp

ROOT = Path(__file__).resolve().parents[1]
TMUX = "/opt/homebrew/bin/tmux"
SIZES = ((120, 36), (80, 24), (160, 45))
TABS = ("scenario", "plan", "result", "session", "drawer")
# launchd identifiers and plist names are data, not untranslated message keys.
MESSAGE_KEY = re.compile(
    r"\bimpl\.[\w.]+|\b(?:power|language|services|network|cli|suggest)\.[a-z]+[\w.]*"
    r"|\b[a-z]+\.[a-z]+_[a-z_]+\b"
)
ENGLISH_UI = re.compile(
    r"\b(?:Language|Quit|Refresh|Review|Disable|Fix|Apply|Freeze|Skip|Status|Background services)\b"
)


def lint_text(text: str, *, language: str, cols: int, rows: int, tab: str) -> list[dict]:
    issues = []

    def issue(kind, detail, line=None):
        issues.append({"kind": kind, "detail": detail, "line": line})

    if "\ufffd" in text:
        issue("replacement-character", "U+FFFD")
    for match in MESSAGE_KEY.finditer(text):
        issue("untranslated-key", match[0])
    if "Traceback" in text or "Error in stylesheet" in text:
        issue("render-exception", "Traceback / stylesheet failure")
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        if text_width(line) > cols:
            issue("overflow", f"{text_width(line)} > {cols}", number)
        if (
            "…" in line
            and "impl." in line
            and not any(message in line for message in ("采样中…", "Sampling…", "正在扫描…", "Scanning…"))
        ):
            issue("truncated", line.strip(), number)
    if len(lines) > rows:
        issue("overflow", f"{len(lines)} > {rows} rows")
    # Inspect the footer and exact controls, not arbitrary app/command names.
    if language == "zh":
        for number, line in enumerate(lines, 1):
            if ENGLISH_UI.search(line) and "Quit Refusal Demo" not in line:
                issue("mixed-language-ui", line.strip(), number)
    language_count = text.count("Language") + text.count("语言")
    if language_count > 1:
        issue("duplicate-language-hint", f"{language_count} hints")
    if not any(line.strip(" ─━│┃╭╮╰╯▁▔") for line in lines[4:-7]):
        issue("blank-tab", tab)
    log_start = next((i for i, line in enumerate(lines) if " Log " in line or " 日志 " in line), None)
    if log_start is not None:
        for line in lines[log_start + 1 : -1]:
            if re.search(r"\berror\b|失败|错误", line, re.I):
                issue("log-error", line.strip())
    return issues


def isolated_env(root: Path) -> dict[str, str]:
    # Remove inherited XDG roots before creating directories in the isolated tree.
    env = {key: value for key, value in os.environ.items() if not key.startswith("XDG_")}
    env["HOME"] = str(root / "home")
    env["TRAVEL_MODE_CONFIG"] = str(root / "config.toml")
    env["TRAVEL_MODE_STATE"] = str(root / "state.json")
    for key in (
        "CONFIG_HOME",
        "STATE_HOME",
        "DATA_HOME",
        "CACHE_HOME",
        "RUNTIME_DIR",
        "CONFIG_DIRS",
        "DATA_DIRS",
    ):
        env["XDG_" + key] = str(root / key.lower())
    env["UV_CACHE_DIR"] = str(root / "uv-cache")
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env.pop("NO_COLOR", None)
    env["FORCE_COLOR"] = "1"
    env["TRAVEL_MODE_LANG"] = "en"
    env.pop("TRAVEL_MODE_DEMO", None)
    for key, value in env.items():
        if key == "HOME" or key.startswith("XDG_") or key == "UV_CACHE_DIR":
            Path(value).mkdir(parents=True, exist_ok=True)
    return env


def copy_config(root: Path, *, live: bool, config: Path | None, host_home: Path) -> dict:
    """Read the source only; all child config writes stay inside the capture tree."""
    source = config if config is not None else host_home / ".config/travel-mode/config.toml" if live else None
    target = root / "config.toml"
    if source is not None and (config is not None or source.exists()):
        shutil.copyfile(source, target)
        target.chmod(0o444)
        return {"kind": "explicit" if config is not None else "user-copy", "path": str(source)}
    # An empty TOML selects the built-in defaults; ensure_conf never touches the host.
    target.write_text("", encoding="utf-8")
    return {"kind": "built-in-default", "reason": "user config missing" if live else "demo"}


def body_has_text(text: str) -> bool:
    lines = text.splitlines()
    log_start = next(
        (i for i, line in enumerate(lines) if " Log " in line or " 日志 " in line), len(lines) - 7
    )
    # Header, tab labels and tab underline occupy rows 0..2. Compact pages can
    # put all their content in rows 3..4; do not skip those first body rows.
    return any(line.strip(" ─━│┃╭╮╰╯▁▔") for line in lines[3:log_start])


def log_has_text(text: str) -> bool:
    lines = text.splitlines()
    log_start = next((i for i, line in enumerate(lines) if " Log " in line or " 日志 " in line), None)
    return log_start is not None and any(line.strip(" ─━│┃╭╮╰╯▁▔") for line in lines[log_start + 1 : -2])


def lint_active_tab(text: str, layout: dict, *, observed: bool) -> list[dict]:
    """Run even on timeout/log arrival, rather than waiting for a healthy layout."""
    if observed and (not layout["body_visible"] or not layout["content_visible"] or not body_has_text(text)):
        return [{"kind": "blank-active-tab", "detail": layout["active"]}]
    return []


class Session:
    def __init__(
        self,
        root: Path,
        cols: int,
        rows: int,
        language: str,
        live: bool,
        config: Path | None = None,
        *,
        setup: bool = False,
    ):
        self.root, self.cols, self.rows, self.language = root, cols, rows, language
        # Live read-only discovery scans the real host; give refresh workers time to settle.
        self.timeout = 120 if live else 30
        self.env = isolated_env(root)
        self.host_home = str(Path.home())
        self.config_source = copy_config(root, live=live, config=config, host_home=Path(self.host_home))
        self.socket = str(root / "tmux.socket")
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv is required")
        command = [
            uv,
            "run",
            "--project",
            str(ROOT),
            "python",
            str(Path(__file__).resolve()),
            "--child",
            "--lang",
            language,
            "--host-home",
            self.host_home,
            "--telemetry",
            str(root / "layout.json"),
        ]
        if setup:
            command.append("--setup")
        if live:
            command.extend(["--live-readonly", "--config", str(root / "config.toml")])
        else:
            command.append("--demo")
        self.tmux("new-session", "-d", "-x", str(cols), "-y", str(rows), "-s", "debug", shlex.join(command))
        self.tmux("set-option", "remain-on-exit", "on")
        self.tmux("set-option", "mouse", "on")

    def tmux(self, *args: str) -> str:
        result = subprocess.run(
            [TMUX, "-f", "/dev/null", "-S", self.socket, *args],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"tmux {args!r} failed ({result.returncode}): {result.stderr.strip()}")
        return result.stdout

    def wait_text(self, text: str, timeout: float | None = None) -> None:
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while time.monotonic() < deadline:
            capture = self.tmux("capture-pane", "-p")
            if "Traceback" in capture or "CSS parsing failed" in capture:
                raise RuntimeError(f"TUI crashed while waiting for {text!r}:\n{capture}")
            telemetry = self.root / "layout.json"
            if text in capture and telemetry.exists() and read_layout(telemetry)["ready"]:
                time.sleep(0.25)
                return
            time.sleep(0.15)
        raise TimeoutError(f"TUI did not show {text!r}:\n{capture}")

    def first_observation(self) -> bool:
        """Stop at the first log or worker completion, including a blocked UI."""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            text = self.tmux("capture-pane", "-p")
            if "Traceback" in text or "CSS parsing failed" in text:
                raise RuntimeError(f"TUI crashed:\n{text}")
            layout = self.root / "layout.json"
            if layout.exists() and (read_layout(layout)["refresh_complete"] or log_has_text(text)):
                return True
            time.sleep(0.1)
        return False

    def wait_started(self) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            path = self.root / "layout.json"
            if path.exists() and read_layout(path)["active"]:
                return
            time.sleep(0.05)
        raise TimeoutError("TUI did not mount its initial tab")

    def wait_complete(self, *, active=None, group=None, details=None, predicate=None) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            layout = read_layout(self.root / "layout.json")
            if (
                layout["ready"]
                and (predicate is None or predicate(layout))
                and (active is None or layout["active"] == active)
                and (group is None or layout["active_filter"] == group)
                and (details is None or layout["details_open"] is details)
            ):
                return
            time.sleep(0.1)
        raise TimeoutError("Requested screen did not finish its committed layout")

    def keys(self, *keys: str) -> None:
        self.tmux("send-keys", *keys)
        time.sleep(0.45)

    def click(self, x: int, y: int) -> None:
        # SGR mouse coordinates are one-based; raw escape bytes reach Textual.
        sequence = f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m"
        self.tmux("send-keys", "-l", sequence)
        time.sleep(0.45)

    def widget_click(self, widget_id: str) -> None:
        for _ in range(80):
            layout = read_layout(self.root / "layout.json")
            widget = next(row for row in layout["widgets"] if row["id"] == widget_id)
            x, y, width, height = widget.get("visible_region", widget["region"])
            if width and height:
                self.click(x + width // 2, y + height // 2)
                return
            x, y, width, height = widget["region"]
            # Scroll the active pane until the full target is visible above the log.
            button = 65 if y >= self.rows - 7 else 64
            self.tmux("send-keys", "-l", f"\x1b[<{button};10;{self.rows // 2}M")
            time.sleep(0.15)
        raise RuntimeError(f"Cannot scroll {widget_id} into the visible pane: {widget['region']}")

    def capture(self, name: str, tab: str, language: str | None = None, *, observed: bool = True) -> dict:
        plain = self.tmux("capture-pane", "-p")
        ansi = self.tmux("capture-pane", "-p", "-e")
        write_text(self.root / f"{name}.txt", plain)
        write_text(self.root / f"{name}.ansi", ansi)
        render(ansi, self.root / f"{name}.png", self.cols, self.rows)
        issues = lint_text(plain, language=language or self.language, cols=self.cols, rows=self.rows, tab=tab)
        layout = read_layout(self.root / "layout.json")
        write_json(self.root / f"{name}.layout.json", layout)
        issues.extend(layout["issues"])
        issues.extend(lint_active_tab(plain, layout, observed=observed))
        expected = {
            "setup": "setup",
            "scenario": "scenario",
            "plan": "plan",
            "result": "result",
            "session": "session",
            "drawer": "result",
            "dashboard": "p-dash",
            "power": "p-power",
            "apps": "p-apps",
            "services": "p-svc",
            "network": "p-net",
        }[tab]
        if layout["active"] != expected:
            issues.append({"kind": "wrong-tab", "detail": f"{layout['active']} != {expected}"})
        return {
            "screenshot": str(self.root / f"{name}.png"),
            "tab": tab,
            "language": language or self.language,
            "size": [self.cols, self.rows],
            "issues": issues,
            "refresh_complete": layout["refresh_complete"],
            "config_source": self.config_source,
        }

    def close(self) -> None:
        self.tmux("kill-server")


class DebugApp(TravelApp):
    BINDINGS: ClassVar = [
        *TravelApp.BINDINGS,
        Binding("f2", "demo_concurrent", "", show=False),
        Binding("f3", "demo_partial", "", show=False),
        Binding("f4", "demo_measurement", "", show=False),
        Binding("f6", "demo_results", "", show=False),
        Binding("f7", "demo_session", "", show=False),
        Binding("f8", "debug_plan", "", show=False),
        Binding("f9", "debug_drawer", "", show=False),
        Binding("f10", "demo_away", "", show=False),
        Binding("f11", "demo_administrator", "", show=False),
        Binding("f12", "demo_pending_restore", "", show=False),
        Binding("ctrl+o", "demo_report", "", show=False),
        Binding("ctrl+g", "demo_health_sources", "", show=False),
    ]
    """TravelApp plus layout telemetry for the tmux debug harness."""

    def __init__(self, telemetry: Path, *, scenario=None):
        super().__init__(scenario=scenario)
        self.telemetry = telemetry
        self.layout_published = asyncio.Event()

    def on_mount(self):
        self.watch_scrolling()
        self.call_after_refresh(self.write_layout)

    def on_page_refreshed(self, event):
        self.watch_scrolling()
        self.call_after_refresh(self.write_layout)

    def watch_scrolling(self):
        from textual.containers import VerticalScroll

        # Recompose replaces containers. Textual deduplicates each reactive watcher.
        for scroll in self.query(VerticalScroll):
            self.watch(scroll, "scroll_y", self.scroll_changed, init=False)

    def scroll_changed(self):
        self.call_after_refresh(self.write_layout)

    def on_worker_state_changed(self, event):
        self.call_after_refresh(self.write_layout)

    def on_tabbed_content_tab_activated(self, event):
        self.call_after_refresh(self.write_layout)

    def on_resize(self):
        self.call_after_refresh(self.write_layout)

    def on_checkbox_changed(self):
        self.call_after_refresh(self.write_layout)

    def logline(self, kind, msg):
        super().logline(kind, msg)
        # A log may precede worker completion. Record the layout of that frame too.
        self.call_after_refresh(self.write_layout)

    def visible_region(self, widget):
        from textual.geometry import Region

        region = widget.region.intersection(self.screen.region)
        for parent in widget.ancestors:
            if parent is self:
                break
            if not parent.display or not parent.visible:
                return Region()
            region = region.intersection(parent.content_region)
        return region

    async def wait_layout(self, predicate):
        async def committed():
            while True:
                self.layout_published.clear()
                if self.telemetry.exists() and predicate(read_layout(self.telemetry)):
                    return
                await self.layout_published.wait()

        await asyncio.wait_for(committed(), 10)

    def write_layout(self) -> bool:
        if self._batch_count:
            return False
        if any(
            (w._layout_required or w._scroll_required) and all(node.display for node in [w, *w.ancestors])
            for w in self.query("*")
        ):
            self.call_after_refresh(self.write_layout)
            return False
        active = "scenario" if self.scenario == "ask" else self.view_mode
        widgets = [
            dict(
                id=w.id,
                type=type(w).__name__,
                region=list(w.region),
                value=getattr(w, "value", None),
                visible_region=list(self.visible_region(w)),
                group=getattr(getattr(getattr(w, "parent", None), "parent", None), "model", {}).get("group"),
                kind=getattr(getattr(getattr(w, "parent", None), "parent", None), "model", {})
                .get("descriptor", {})
                .get("kind"),
            )
            for w in self.query("*")
            if w.display and w.visible and w.region.width
        ]
        body = self.main_query_one("#scenario-picker" if active == "scenario" else "#body")
        body_visible = bool(self.visible_region(body).area)
        content_visible = any(self.visible_region(w).area for w in body.query("Static, Button, Checkbox"))
        complete = all(
            not worker.is_running for worker in self.workers if worker.group in {"dash", "audit", "filter"}
        )
        ready = complete and (self.refresh_complete or active == "scenario")
        write_layout(
            self.telemetry,
            dict(
                schema_version=1,
                active=active,
                widgets=widgets,
                issues=[],
                ready=ready,
                pages_ready=sorted(self.pages_ready),
                refresh_complete=ready,
                body_visible=body_visible,
                content_visible=content_visible,
                action_generation=self._mutation_generation,
                mutation_busy=self._mutation_lock.locked(),
                passive_before_intervals=(self.passive_measurement.before or {}).get("interval_count", 0),
                passive_after_intervals=((self.st.get("measurement") or {}).get("after") or {}).get(
                    "interval_count", 0
                ),
                focused_id=self.focused.id if self.focused else None,
                active_filter=self.active_filter,
                details_open=self.details_open,
                plan_rows=[
                    dict(
                        id=r["id"],
                        kind=r["descriptor"]["kind"],
                        name=r["name"],
                        selected=r["selected"],
                        state=r["state"],
                        result_member=r.get("result_member", False),
                    )
                    for r in self.plan_items
                ],
            ),
        )
        self.layout_published.set()
        return True

    async def action_demo_concurrent(self):
        if getattr(self, "live_readonly", False):
            return
        self.view_mode = "result"
        self.batch_running = True
        self.active_filter = "apps"
        for row in self.plan_items:
            member = row["descriptor"]["kind"] == "app" and row["action"] not in {"skip", "ask"}
            row.update(result_member=member, state="running" if member else "skipped", result="")
        await self.render_plan()
        self.main_query_one("#body").scroll_home(animate=False, immediate=True)
        self.call_after_refresh(self.write_layout)

    async def action_demo_partial(self):
        if getattr(self, "live_readonly", False):
            return
        for row in [r for r in self.plan_items if r.get("result_member")][:2]:
            row.update(state="done", result=t("redesign.row_done"))
        await self.render_plan()
        self.call_after_refresh(self.write_layout)

    async def action_demo_measurement(self):
        if getattr(self, "live_readonly", False):
            return

        index = getattr(self, "demo_measure_index", 0)
        self.demo_measure_index = index + 1
        await self.action_demo_session()
        before, after = (8.8, 8.5) if index == 1 else (23.1, 13.0)
        count = 1 if index == 1 else 2
        self.st["measurement"] = dict(
            mode="passive",
            before=dict(available=True, average_watts=before, interval_count=count),
            after=None if index == 0 else dict(available=True, average_watts=after, interval_count=count),
            saved_watts=None if index == 0 else before - after,
            extra_hours=None,
        )
        if index == 1:
            # An old saved session must render through the current threshold too.
            self.st["measurement"].pop("mode")
            self.st["measurement"]["before"]["average_watts"] = 8.82
            self.st["measurement"]["after"]["average_watts"] = 8.54
            self.st["measurement"].update(saved_watts=0.28, extra_hours=0.18)
        self.update_session()
        self.main_query_one("#body").scroll_home(animate=False, immediate=True)
        self.call_after_refresh(self.write_layout)

    async def action_demo_results(self):
        if getattr(self, "live_readonly", False):
            return
        self.view_mode = "result"
        self.batch_running = False
        self.active_filter = "apps" if self.size.width <= 80 else "all"
        for row in self.plan_items:
            row.update(
                state="done" if row["selected"] and row["action"] not in {"ask", "skip"} else "skipped",
                result_member=row["selected"] and row["action"] not in {"ask", "skip"},
                selected=False,
                result=t("redesign.row_done") if row["selected"] else "",
            )
        app_rows = [
            row for row in self.plan_items if row["descriptor"]["kind"] == "app" and row["state"] == "done"
        ]
        if app_rows:
            warning = next((r for r in app_rows if r["name"] == "Google Drive"), app_rows[0])
            warning.update(
                state="warning",
                result=t("failure.forced_refused", name=warning["name"]),
                warning_key="failure.forced_refused",
            )
        if len(app_rows) > 1:
            failure = next((r for r in app_rows if r["name"] == "Quit Refusal Demo"), app_rows[1])
            failure.update(
                state="failed",
                quit_status="refused",
                app_running=True,
                result=t("failure.incomplete"),
                reason=t("failure.quit_refused", name=failure["name"]),
            )
            failure.setdefault("detail_fields", {}).update(
                command="osascript quit", returncode=1, apple_event=-128
            )
            self.focused_plan_id = failure["id"]
        self.plan_items.sort(
            key=lambda row: (
                0 if row["group"] == "power" else 1,
                0 if row["state"] == "failed" else 1 if row["state"] == "warning" else 2,
            )
        )
        await self.render_plan()
        self.publish_result_summary()
        self.main_query_one("#body").scroll_home(animate=False, immediate=True)
        self.call_after_refresh(self.write_layout)

    async def action_demo_session(self):
        if getattr(self, "live_readonly", False):
            return
        self.batch_running = False
        from datetime import datetime, timedelta, timezone

        from travel_mode import sessions

        sessions.begin(self.st)
        active = self.st["session"]
        active["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=80)).isoformat()
        active["start"].update(
            percent=80, remaining_mah=5000, telemetry=dict(system_count=100, system_sum_mw=1000000)
        )
        active["external_seen"] = False
        self.current_battery = dict(
            available=True,
            external_connected=False,
            is_charging=False,
            remaining_mah=3200,
            maximum_mah=5000,
            remaining_wh=36,
            telemetry=dict(system_count=110, system_sum_mw=1098000),
            power_source="PowerTelemetryData.SystemLoad",
        )
        self.current_estimate = dict(
            average_watts=9.8, remaining_minutes=220, estimate_source="session_telemetry", estimate_error=None
        )
        self.main_query_one("#dash-battery").update(self.battery_heading(self.current_battery))
        self.st["scenario"] = "work"
        self.st["batt_prof"] = "lowpowermode 0 powernap 1 womp 1"
        self.st["frozen_apps"] = ["/Demo/Slack.app", "/Demo/Discord.app"]
        self.st["unloaded_svcs"] = ["/tmp/demo.plist"]
        self.resurrected = [dict(kind="app", name="Google Drive", path="/Demo/Google Drive.app")]
        self.merged = True
        self.view_mode = "session"
        self.update_primary()
        self.update_session()
        await self.render_attention()
        self.call_after_refresh(self.write_layout)

    def action_demo_administrator(self):
        if getattr(self, "live_readonly", False):
            return
        from travel_mode.tui.administrator import AdministratorScreen

        self.push_screen(AdministratorScreen([(["mdutil", "-i", "on", "/"], t("admin.restore_spotlight"))]))
        self.call_after_refresh(self.write_layout)

    def action_demo_health_sources(self):
        if getattr(self, "live_readonly", False):
            return
        from travel_mode.demo import _configure_health_sources

        _configure_health_sources()
        self.action_refresh()

    def action_demo_report(self):
        if getattr(self, "live_readonly", False):
            return
        import json

        from travel_mode import sessions
        from travel_mode.tui.session_screen import SessionScreen

        # Public deterministic fixture only; never read a user's history.
        report = json.loads((ROOT / "tests/fixtures/session-wording.json").read_text())
        report["measurement"] = dict(
            before=dict(available=True, average_watts=8.82, interval_count=1),
            after=dict(available=True, average_watts=8.54, interval_count=1),
            saved_watts=0.28,
            extra_hours=0.18,
        )
        report["errors"] = ["pmset -g log: timeout (124)"]
        report["failures"].append(
            dict(
                at=report["ended_at"],
                scenario="work",
                action=t("failure.worker", group="battery"),
                target="",
                command=[],
                rc=None,
                reason="old read error",
                stdout="",
                stderr="",
                kind="internal",
                counts=dict(attempted=14, succeeded=11, failed=3),
            )
        )
        report["actions"] = dict(attempted=14, succeeded=11, failed=3)
        self.view_mode = "session"
        self.update_primary()
        self.push_screen(SessionScreen("\n".join(sessions.describe(report))))
        self.call_after_refresh(self.write_layout)

    async def action_demo_pending_restore(self):
        if getattr(self, "live_readonly", False):
            return
        from travel_mode import failures, state

        await self.action_demo_session()
        self.st["spotlight_off"] = "1"
        failures.record(
            self.st,
            t("admin.restore_spotlight"),
            t("sudo.not_provided"),
            [dict(command=["sudo", "mdutil", "-i", "on", "/"], rc=77, stdout="", stderr="")],
            kind="not_executed",
            target="restore:spotlight_off",
        )
        state.save_state(self.st)
        await self.render_restore_pending()
        self.logline(
            "bad",
            t(
                "admin.restore_pending",
                n=1,
                names=t(
                    "admin.pending_item", name=t("admin.item_spotlight"), reason=t("admin.password_reason")
                ),
            ),
        )
        self.call_after_refresh(self.write_layout)

    def action_demo_away(self):
        if getattr(self, "live_readonly", False):
            return
        from travel_mode.state import new_state

        self.scenario = "away"
        self.st, self.merged = new_state(), False
        self._session_admitted = True
        self.plan_items = []
        self.active_filter = "all"
        self.view_mode = "plan"
        self.action_refresh()

    def action_debug_drawer(self):
        self.action_details()
        self.call_after_refresh(self.write_layout)

    def action_debug_plan(self):
        self.view_mode = "plan"
        self.action_refresh()


def child(args) -> int:
    """No real write-capable runner is installed in either debug mode."""
    from contextlib import ExitStack

    from travel_mode.i18n import set_language
    from travel_mode.system.shell import ReadOnlyRunner, set_runner

    with ExitStack() as demo_scope:
        if args.live_readonly:
            set_runner(ReadOnlyRunner())
            # Explicit read-only discovery roots; HOME remains isolated for every write.
            from travel_mode.system.launchd import svc_scan

            apps.APP_DIRS = ["/Applications", str(Path(args.host_home) / "Applications")]
            launchd.svc_scan = lambda config=None: svc_scan(
                config or config_api.load_config(),
                directories=(Path(args.host_home) / "Library/LaunchAgents", Path("/Library/LaunchAgents")),
            )
        else:
            from travel_mode.demo import _configure_demo_views, environment

            demo_scope.enter_context(environment(Path(args.telemetry).parent))
            _configure_demo_views()
        set_language(args.lang)
        if args.setup:
            setup_debug_app(Path(args.telemetry)).run()
        else:
            app = DebugApp(Path(args.telemetry))
            app.live_readonly = args.live_readonly
            app.run()
        return 0


def setup_debug_app(telemetry):
    """Publish the visible setup screen, never a second hidden base-class screen."""
    from travel_mode.tui.setup import SetupApp, SetupScreen

    class DebugSetupScreen(SetupScreen):
        async def on_mount(self, event):
            event.prevent_default()
            await super().on_mount()
            self.call_after_refresh(self.publish)

        def on_resize(self, event):
            event.prevent_default()
            super().on_resize()
            self.call_after_refresh(self.publish)

        def action_column(self, scenario):
            super().action_column(scenario)
            self.call_after_refresh(self.publish)

        def on_descendant_focus(self):
            self.call_after_refresh(self.publish)

        def reveal_focused_row(self):
            super().reveal_focused_row()
            self.call_after_refresh(self.publish)

        def publish(self):
            ready = self.ready.is_set()
            write_layout(
                telemetry,
                {
                    "schema_version": 1,
                    "active": "setup",
                    "setup_reviewed": self.reviewed.is_set(),
                    "setup_column": self.column,
                    "widgets": [
                        dict(
                            id=w.id,
                            type=type(w).__name__,
                            region=list(w.region),
                            value=getattr(w, "value", None),
                        )
                        for w in self.query("*")
                    ],
                    "issues": [],
                    "ready": ready,
                    "pages_ready": ["setup"] if ready else [],
                    "refresh_complete": ready,
                    "body_visible": True,
                    "content_visible": bool(self.rows),
                    "action_generation": 0,
                    "mutation_busy": False,
                    "focused_id": self.focused.id if self.focused else None,
                },
            )

    class DebugSetupApp(SetupApp):
        def on_mount(self, event):
            event.prevent_default()
            self.push_screen(DebugSetupScreen(dry_run=True), self.exit)

    return DebugSetupApp(dry_run=True)


def write_report(output: Path, results: list[dict], mode: str, error: str | None = None) -> dict:
    """lint.json is written for both modes and for failed runs, so checks always have evidence."""
    issues = sum(len(result["issues"]) for result in results)
    report = {
        "mode": mode,
        "captures": results,
        "issue_count": issues,
        "operation_sequence": [
            "scenario: capture before choosing 1 + Enter (work)",
            "plan: capture default selection; brightness, Wi-Fi and Bluetooth remain unchecked",
            "plan-network: 5, End; visibility and scroll only, no selection changes",
            "result: 1, Home, F6; synthetic selected-row outcomes only; "
            "no Action execution or selection changes",
            "drawer: F9; focus the selected Quit Refusal Demo failure",
            "session: F9, F7; synthetic session endpoints and one resurrected app",
            "plan-away: F10; clear synthetic session and collect away defaults",
        ],
    }
    if error:
        report["failure"] = error
        report["issue_count"] = issues + 1
    write_json(output / "lint.json", report)
    return report


def run_group(output: Path, cols: int, rows: int, language: str, args, results: list[dict]) -> None:
    if args.setup:
        root = output / f"{cols}x{rows}-{language}" / "setup"
        session = Session(root, cols, rows, language, args.live_readonly, args.config, setup=True)
        try:
            session.wait_started()
            session.wait_complete()
            results.append(session.capture("setup", "setup"))
            session.keys("Right", "End")
            deadline = time.monotonic() + session.timeout
            while time.monotonic() < deadline:
                layout = read_layout(session.root / "layout.json")
                app_ids = [w["id"] for w in layout["widgets"] if w["id"] and w["id"].startswith("setup-app-")]
                focused = next(w for w in layout["widgets"] if w["id"] == app_ids[-1])
                body = next(w for w in layout["widgets"] if w["id"] == "setup-body")
                _, fy, _, fh = focused["region"]
                _, by, _, bh = body["region"]
                if (
                    layout["setup_column"] == "away"
                    and layout["focused_id"] == app_ids[-1]
                    and by <= fy
                    and fy + fh <= by + bh
                ):
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError("Setup did not focus and reveal the last app")
            results.append(session.capture("setup-away-last", "setup"))
            review = next(row for row in layout["widgets"] if row["id"] == "setup-review")
            x, y, width, height = review["region"]
            session.click(x + width // 2, y + height // 2)
            deadline = time.monotonic() + session.timeout
            while time.monotonic() < deadline:
                layout = read_layout(session.root / "layout.json")
                if layout["setup_reviewed"] and layout["focused_id"] == "setup-save":
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError("Setup summary and Save did not finish layout")
            results.append(session.capture("setup-reviewed", "setup"))
        finally:
            session.close()
        return
    root = output / f"{cols}x{rows}-{language}"
    session = Session(root, cols, rows, language, args.live_readonly, args.config)
    try:
        session.wait_started()
        if read_layout(session.root / "layout.json")["active"] == "scenario":
            results.append(session.capture("scenario", "scenario"))
            session.keys("1", "Enter")
        session.wait_complete(active="plan")
        if args.health_sources:
            session.keys("C-g")
            session.wait_complete(active="plan")
            if rows <= 26:
                session.widget_click("b-suggestions")
            session.wait_text("Claude Code")
            results.append(session.capture("health-sources", "plan"))
            return
        if args.session_report:
            session.keys("C-o")
            from travel_mode.i18n import catalog

            session.wait_text(catalog(language)["session.heading"].split("{scenario}")[0])
            results.append(session.capture("session-report", "session"))
            return
        if args.admin:
            session.keys("F12")
            session.wait_complete(active="session")
            results.append(session.capture("restore-pending", "session"))
            session.keys("F11")
            from travel_mode.i18n import catalog

            session.wait_text(catalog(language)["admin.title"])
            results.append(session.capture("administrator", "session"))
            return
        results.append(session.capture("plan", "plan"))
        if args.fast_apply:
            from travel_mode.i18n import catalog

            session.keys("F2")
            session.wait_complete(active="result", group="apps")
            results.append(session.capture("concurrent-running", "result"))
            session.keys("F3")
            session.wait_complete(
                active="result",
                predicate=lambda layout: any(row["state"] == "done" for row in layout["plan_rows"]),
            )
            results.append(session.capture("concurrent-partial", "result"))
            for name, key in (
                ("waiting", "measurement.passive_wait"),
                ("noise", "measurement.passive_noise"),
                ("saving", "measurement.passive_comparison"),
            ):
                session.keys("F4")
                session.wait_complete(active="session")
                session.wait_text(
                    catalog(language)[key] if name == "waiting" else "8.8 W" if name == "noise" else "23.1 W"
                )
                results.append(session.capture("measurement-" + name, "session"))
            return
        if not args.live_readonly:
            session.keys("5")
            session.wait_complete(active="plan", group="network")
            session.keys("End")
            session.wait_complete(active="plan", group="network")
            results.append(session.capture("plan-network", "plan"))
            session.keys("1")
            session.wait_complete(active="plan", group="all")
            session.keys("Home")
            session.wait_complete(active="plan", group="all")
            session.keys("F6")
            session.wait_complete(active="result")
            results.append(session.capture("result", "result"))
            session.keys("F9")
            session.wait_complete(active="result", details=True)
            results.append(session.capture("drawer", "drawer"))
            session.keys("F9", "F7")
            session.wait_complete(active="session", details=False)
            results.append(session.capture("session", "session"))
            session.keys("F10")
            session.wait_complete(active="plan", group="all")
            results.append(session.capture("plan-away", "plan"))
        else:
            session.keys("d")
            session.wait_complete(active="plan", details=True)
            results.append(session.capture("plan-details", "plan"))
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fast-apply", action="store_true", help="capture concurrent results and passive power states"
    )
    parser.add_argument("--setup", action="store_true", help="capture the app defaults setup screen")
    parser.add_argument(
        "--admin", action="store_true", help="capture administrator confirmation and pending recovery"
    )
    parser.add_argument("--session-report", action="store_true", help="capture the readable session report")
    parser.add_argument(
        "--health-sources", action="store_true", help="capture observed keep-awake and CPU sources"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="scenario, plan, result, session and drawer × 2 languages × 3 sizes",
    )
    parser.add_argument("--config", type=Path, help="copy this config read-only into each isolated session")
    parser.add_argument("--jobs", type=int, default=1, help="parallel isolated tmux groups (default: 1)")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--live-readonly", action="store_true")
    parser.add_argument("--lang", choices=("en", "zh"), default="en")
    parser.add_argument("--size", choices=("120x36", "80x24", "160x45"), default="120x36")
    parser.add_argument("--keys", nargs="*", default=[])
    parser.add_argument("--click", nargs=2, type=int, metavar=("X", "Y"))
    parser.add_argument("--wait-text")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-home", help=argparse.SUPPRESS)
    parser.add_argument("--telemetry", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.config is not None and not args.config.is_file():
        parser.error(f"--config is not a file: {args.config}")
    if args.live_readonly and args.config is None:
        parser.error("--live-readonly requires an explicit --config copy")
    if args.fast_apply and args.live_readonly:
        parser.error("--fast-apply requires demo fixtures")
    if args.admin and args.live_readonly:
        parser.error("--admin requires demo fixtures")
    if args.session_report and args.live_readonly:
        parser.error("--session-report requires demo fixtures")
    if args.health_sources and args.live_readonly:
        parser.error("--health-sources requires demo fixtures")
    if args.demo and args.live_readonly:
        parser.error("--demo and --live-readonly are mutually exclusive")
    if args.child:
        return child(args)
    output = args.output or Path("/tmp/tui-debug") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    mode = "live-readonly" if args.live_readonly else "demo"
    results: list[dict] = []
    sizes = SIZES if args.all else (tuple(map(int, args.size.split("x"))),)
    languages = ("en", "zh") if args.all else (args.lang,)
    error = None
    try:
        groups = [(cols, rows, language) for cols, rows in sizes for language in languages]
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = [
                executor.submit(run_group, output, cols, rows, lang, args, results)
                for cols, rows, lang in groups
            ]
            for future in futures:
                future.result()
    except (RuntimeError, TimeoutError, AssertionError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        report = write_report(output, results, mode, error)
    if error is not None:
        print(error, file=sys.stderr)
        print(f"Failed run — lint.json still written: {output}")
        return 1
    print("Size      Language  Captures  Issues")
    for cols, rows in sizes:
        for language in languages:
            group = [row for row in results if f"{cols}x{rows}-{language}" in row["screenshot"]]
            print(f"{cols}x{rows:<5} {language:<9} {len(group):<9} {sum(len(r['issues']) for r in group)}")
    print(f"Screenshots and lint.json: {output}")
    return int(bool(report["issue_count"]))


if __name__ == "__main__":
    raise SystemExit(main())

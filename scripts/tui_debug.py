"""Real tmux TUI debug matrix. Every child has isolated HOME/XDG/config/state."""

from __future__ import annotations

import argparse
import json
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

from terminal_render import render, text_width

import travel_mode.config as config_api
from travel_mode.system import apps, launchd
from travel_mode.tui.app import TravelApp

ROOT = Path(__file__).resolve().parents[1]
TMUX = "/opt/homebrew/bin/tmux"
SIZES = ((120, 36), (80, 24), (160, 45))
TABS = ("dashboard", "power", "apps", "services", "network")
PANELS = {
    "p-dash": "dash-status",
    "p-power": "power-list",
    "p-apps": "apps-box",
    "p-svc": "svc-box",
    "p-net": "net-box",
}
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
        if "…" in line and not any(
            message in line for message in ("采样中…", "Sampling…", "正在扫描…", "Scanning…")
        ):
            issue("truncated", line.strip(), number)
    if len(lines) > rows:
        issue("overflow", f"{len(lines)} > {rows} rows")
    # Inspect the footer and exact controls, not arbitrary app/command names.
    if language == "zh":
        for number, line in enumerate(lines, 1):
            if ENGLISH_UI.search(line):
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
    return any(line.strip(" ─━│┃╭╮╰╯▁▔") for line in lines[5:log_start])


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
        self, root: Path, cols: int, rows: int, language: str, live: bool, config: Path | None = None
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
        if live:
            command.append("--live-readonly")
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
            if text in capture and telemetry.exists() and json.loads(telemetry.read_text())["ready"]:
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
            if layout.exists() and (json.loads(layout.read_text())["refresh_complete"] or log_has_text(text)):
                return True
            time.sleep(0.1)
        return False

    def wait_started(self) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            path = self.root / "layout.json"
            if path.exists() and json.loads(path.read_text())["active"]:
                return
            time.sleep(0.05)
        raise TimeoutError("TUI did not mount its initial tab")

    def wait_complete(self) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if json.loads((self.root / "layout.json").read_text())["ready"]:
                time.sleep(0.25)
                return
            time.sleep(0.1)
        raise TimeoutError("Refresh workers did not complete")

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
            layout = json.loads((self.root / "layout.json").read_text())
            widget = next(row for row in layout["widgets"] if row["id"] == widget_id)
            x, y, width, height = widget["region"]
            if width and height and y >= 3 and y + height <= self.rows - 7:
                self.click(x + 2, y + height // 2)
                return
            # Scroll the active pane until the full target is visible above the log.
            button = 65 if y >= self.rows - 7 else 64
            self.tmux("send-keys", "-l", f"\x1b[<{button};10;{self.rows // 2}M")
            time.sleep(0.15)
        raise RuntimeError(f"Cannot scroll {widget_id} into the visible pane: {widget['region']}")

    def capture(self, name: str, tab: str, language: str | None = None, *, observed: bool = True) -> dict:
        plain = self.tmux("capture-pane", "-p")
        ansi = self.tmux("capture-pane", "-p", "-e")
        (self.root / f"{name}.txt").write_text(plain, encoding="utf-8")
        (self.root / f"{name}.ansi").write_text(ansi, encoding="utf-8")
        render(ansi, self.root / f"{name}.png", self.cols, self.rows)
        issues = lint_text(plain, language=language or self.language, cols=self.cols, rows=self.rows, tab=tab)
        layout = json.loads((self.root / "layout.json").read_text())
        (self.root / f"{name}.layout.json").write_text(json.dumps(layout, indent=2))
        issues.extend(layout["issues"])
        issues.extend(lint_active_tab(plain, layout, observed=observed))
        expected = {
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
    """TravelApp plus layout telemetry for the tmux debug harness."""

    def __init__(self, telemetry: Path):
        super().__init__()
        self.telemetry = telemetry

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

    def write_layout(self) -> bool:
        # batch_update 内 DOM 可处在 remove/mount 之间，此时尚未绘制。
        # 只记录已绘制帧；PageRefreshed 事件会在页挂载结束后再次采集。
        if self._batch_count:
            return False
        from textual.css.query import NoMatches
        from textual.widgets import Button, Checkbox, Label, Static, TabbedContent

        widgets, issues = [], []
        active = ""
        body_visible = content_visible = False
        try:
            for widget in self.query("*"):
                if not widget.visible or not widget.display or not widget.region.width:
                    continue
                region = widget.region
                widgets.append(
                    {
                        "id": widget.id,
                        "type": type(widget).__name__,
                        "region": list(region),
                        "value": getattr(widget, "value", None),
                    }
                )
                # Allow pane scrolling, but reject controls clipped by their own row.
                if (
                    isinstance(widget, (Button, Checkbox, Label, Static))
                    and widget.parent
                    and widget.parent.has_class("row")
                    and not widget.parent.region.contains_region(region)
                ):
                    issues.append({"kind": "clipped-control", "detail": widget.id or type(widget).__name__})
            active = self.query_one(TabbedContent).active
            # TabbedContent.active is a reactive whose default is "" (also set on
            # Tabs.Cleared); recompose windows expose it. Never let a telemetry
            # tick kill the TUI under observation — surface it as a lint issue.
            panel = self.query_one("#" + PANELS[active]) if active in PANELS else None
            if panel is None:
                issues.append({"kind": "no-active-panel", "detail": active})
            else:
                # At the top, validate the sampled panel (not just surrounding controls).
                # After scrolling, that panel may be offscreen while other page content is visible.
                pane = self.query_one("#" + active)
                scrolled = any(parent.scroll_y > 0 for parent in panel.ancestors if parent is not self)
                visible_body = pane if scrolled else panel
                body_visible = bool(self.visible_region(visible_body).area)
                content_visible = any(
                    self.visible_region(widget).area
                    for widget in visible_body.query("Label, Static, Button, Checkbox")
                )
            # Worker completion follows all awaited mounts; logs may appear before it.
            complete = all(
                not worker.is_running for worker in self.workers if worker.group in {"dash", "audit"}
            )
            ready = complete and self.refresh_complete
        except NoMatches as error:
            issues.append({"kind": "telemetry-error", "detail": f"{type(error).__name__}: {error}"})
            ready = complete = False
        payload = {
            "active": active,
            "widgets": widgets,
            "issues": issues,
            "ready": ready,
            "pages_ready": sorted(self.pages_ready),
            "refresh_complete": ready,
            "body_visible": body_visible,
            "content_visible": content_visible,
        }
        temporary = self.telemetry.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(self.telemetry)
        return True


def child(args) -> int:
    """No real write-capable runner is installed in either debug mode."""
    from travel_mode.i18n import set_language
    from travel_mode.system.shell import ReadOnlyRunner, set_runner

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
        from travel_mode.demo import _configure_demo, _configure_demo_views

        _configure_demo()
        _configure_demo_views()
    set_language(args.lang)
    DebugApp(Path(args.telemetry)).run()
    return 0


def write_report(output: Path, results: list[dict], mode: str, error: str | None = None) -> dict:
    """lint.json is written for both modes and for failed runs, so checks always have evidence."""
    issues = sum(len(result["issues"]) for result in results)
    report = {"mode": mode, "captures": results, "issue_count": issues}
    if error:
        report["failure"] = error
        report["issue_count"] = issues + 1
    (output / "lint.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_group(output: Path, cols: int, rows: int, language: str, args, results: list[dict]) -> None:
    # Each initial-tab probe is a fresh process: no earlier tab switch can repair layout.
    for key, tab in enumerate(TABS, 1):
        root = output / f"{cols}x{rows}-{language}" / f"initial-{tab}"
        session = Session(root, cols, rows, language, args.live_readonly, args.config)
        try:
            session.wait_started()
            session.keys(str(key))
            observed = session.first_observation()
            results.append(session.capture("first-observation", tab))
            if not observed:
                raise TimeoutError(f"No refresh completion or log for initial {tab}")
            session.wait_complete()
            results.append(session.capture("refresh-complete", tab))
        finally:
            session.close()
    root = output / f"{cols}x{rows}-{language}"
    session = Session(root, cols, rows, language, args.live_readonly, args.config)
    try:
        session.wait_text("SleepDisabled")
        if args.wait_text:
            session.wait_text(args.wait_text)
        if args.keys:
            session.keys(*args.keys)
        if args.click:
            session.click(*args.click)
        for key, tab in enumerate(TABS, 1):
            session.keys(str(key))
            results.append(session.capture(tab, tab))
            if tab in ("dashboard", "power", "services", "network"):
                session.keys("End")
                results.append(session.capture(tab + "-bottom", tab))
                session.keys("Home")
        if cols == 120:
            session.keys("1")
            before = json.loads((root / "layout.json").read_text())
            value = next(w["value"] for w in before["widgets"] if w["id"] == "q-power")
            session.widget_click("q-power")
            after = json.loads((root / "layout.json").read_text())
            changed_value = next(w["value"] for w in after["widgets"] if w["id"] == "q-power")
            if changed_value == value:
                raise AssertionError("Mouse click did not toggle q-power")
            results.append(session.capture("checked", "dashboard"))
            # A harmless refresh supplies real log content in both modes.
            session.keys("r")
            results.append(session.capture("log", "dashboard"))
            session.keys("l")
            changed = "zh" if language == "en" else "en"
            session.wait_text("语言" if changed == "zh" else "Language")
            results.append(session.capture("language-switch", "dashboard", changed))
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="5 tabs × 2 languages × 3 sizes plus key states")
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

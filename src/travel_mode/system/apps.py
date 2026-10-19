"""System adapter: apps."""

from __future__ import annotations

import os
import plistlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_config
from ..i18n import t
from .shell import is_dry_run, run_rc, run_result

APP_DIRS = [
    "/Applications",
    str(Path.home() / "Applications"),
    "/Applications/Utilities",
    "/System/Applications",
]


def app_directories():
    """Standard locations plus explicit absolute TRAVEL_MODE_APP_DIRS entries."""
    extra = os.environ.get("TRAVEL_MODE_APP_DIRS")
    paths = extra.split(os.pathsep) if extra is not None else []
    if any(not value or not Path(value).is_absolute() for value in paths):
        raise ValueError(t("apps.invalid_directories"))
    return list(dict.fromkeys([*paths, *APP_DIRS]))


def app_path(name):
    from ..system.app_snapshot import AppIndex, current_snapshot

    snapshot = current_snapshot()
    return (snapshot.index if snapshot else AppIndex(app_directories())).find(name)


def app_bid(path):
    plist = Path(path) / "Contents" / "Info.plist"
    try:
        with plist.open("rb") as f:
            metadata = plistlib.load(f)
        if not isinstance(metadata, dict):
            raise ValueError(t("safety.expected_dictionary", path=plist))
        identifier = metadata.get("CFBundleIdentifier", "")
        if not isinstance(identifier, str):
            raise ValueError(t("safety.invalid_bundle_id", path=plist))
        return identifier
    except FileNotFoundError:
        # 缺少可选 bundle id 时按显示名退出；损坏的元数据必须显式失败。
        return ""


def app_running(path):
    from ..system.app_snapshot import current_snapshot

    snapshot = current_snapshot()
    if snapshot is not None:
        return bool(path) and bool(snapshot.matching("^" + re.escape(path) + "/Contents/MacOS/"))
    from ..system.process_control import app_processes

    return bool(path) and bool(app_processes(path, main_only=True))


def bundle_alive(path):
    from ..system.app_snapshot import current_snapshot

    snapshot = current_snapshot()
    if snapshot is not None:
        return bool(path) and bool(snapshot.matching("^" + re.escape(path) + "/"))
    from ..system.process_control import app_processes

    return bool(path) and bool(app_processes(path, whole_bundle=True))


@dataclass(frozen=True)
class QuitResult:
    status: str
    exited: bool
    rc: int = 0
    stdout: str = ""
    stderr: str = ""
    command: list = field(default_factory=list)
    helpers_remaining: bool = False

    def __bool__(self):
        return self.exited

    def explanation(self, name):
        if self.helpers_remaining:
            return t("failure.quit_helpers", name=name)
        if self.status == "not_running" and not self.exited:
            return t("failure.quit_disagrees", name=name)
        return t("failure.quit_" + self.status, name=name)


def grace_quit(path, name):
    from .app_snapshot import live_process_scope

    with live_process_scope():
        return _grace_quit(path, name)


def _grace_quit(path, name):
    """AppleScript quit (8s 超时) + 轮询 10s. 没在跑直接成功."""
    if not app_running(path):
        return QuitResult("not_running", True)
    if load_config().apps.policy == "adaptive":
        from .process_control import _protected, process_table

        _protected(path, process_table())
    bid = app_bid(path)
    target = bid or name
    if not isinstance(target, str) or not target:
        raise ValueError(t("safety.missing_app_identity", path=path))
    quoted = target.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")
    tell = f'tell application {"id " if bid else ""}"{quoted}" to quit'
    from ..actions.journal import active_state, record_change

    if active_state() is not None:
        record_change("quit_apps", path)
    command = ["osascript", "-e", "with timeout of 8 seconds", "-e", tell, "-e", "end timeout"]
    result = run_result(command, timeout=10)
    status = "exited"
    if result.rc:
        detail = result.stdout + result.stderr
        status = (
            "refused"
            if re.search(r"\(-128\)", detail)
            else "timeout"
            if result.rc == 124 or re.search(r"\(-1712\)", detail)
            else "not_running"
            if re.search(r"\(-600\)", detail)
            else "error"
        )
        if status not in {"refused", "timeout"}:
            return QuitResult(status, not app_running(path), result.rc, result.stdout, result.stderr, command)
    if is_dry_run():
        return QuitResult("exited", True, command=command)
    # Apple Events can be cancelled while an Electron app is already cleaning up.
    # Share the same finite ten-second verification window for all quit outcomes.
    main_exited = False
    for step in range(21):
        main_exited = not app_running(path)
        if main_exited and not bundle_alive(path):
            return QuitResult("exited", True, result.rc, result.stdout, result.stderr, command)
        if step < 20:
            time.sleep(0.5)
    return QuitResult(
        "exited" if main_exited else "timeout" if status == "exited" else status,
        main_exited,
        result.rc,
        result.stdout,
        result.stderr,
        command,
        helpers_remaining=main_exited,
    )


def force_kill(path):
    """只杀主进程+Frameworks/Helpers, 不碰 Resources/PlugIns (同 app_running 的理由)."""
    from ..system.process_control import force_kill as stop

    return stop(path)


def freeze_app(path, state=None, save=None):
    """SIGSTOP 冻结同范围进程, off 时 CONT 秒回零数据丢失."""
    from ..system.process_control import freeze_app as stop

    return stop(path, state, save)


def frozen_count(path):
    from ..system.process_control import app_processes

    return sum(row.stat.startswith("T") for row in app_processes(path))


def freeze_result_count(path):
    """Count planned exact PID receipts in dry-run, observed stopped processes otherwise."""
    if not is_dry_run():
        return frozen_count(path)
    from ..actions.journal import active_state
    from ..state import StateError
    from .shell import dry_run_commands

    state = active_state()
    if state is None:
        raise StateError(t("safety.journal_required", field="freeze result count"))
    planned = {cmd[2] for cmd in dry_run_commands() if cmd[:2] == ["kill", "-STOP"]}
    return sum(row["path"] == path and str(row["pid"]) in planned for row in state.get("frozen_pids", []))


def app_sleep_assertions(name):
    """该 app 名字命中的防睡断言行. 冻结不释放断言 — 命中时建议改为退出."""
    atext, rc = run_rc(["pmset", "-g", "assertions"], timeout=20)
    if rc:
        from ..failures import CommandError

        raise CommandError(["pmset", "-g", "assertions"], rc, atext)
    return [
        line.strip()
        for line in atext.splitlines()
        if re.search(r"Prevent(?:UserIdle)?SystemSleep", line) and name.lower() in line.lower()
    ]


def load_conf():
    from ..config import load_config

    config = load_config()
    return config.apps.actions, config.apps.default


def ensure_conf():
    return False


def scan_apps():
    """返回 {running: [{name,path,cpu,defact}], nobundle: [str]}"""
    from ..system.app_snapshot import snapshot_scope

    with snapshot_scope() as snapshot:
        from ..categories import app_action, identify, ignored, metadata

        config = load_config()
        running, nobundle, seen = [], [], set()
        scoped = (
            getattr(config.apps, config.options.scenario, {})
            if config.options.scenario in {"work", "away"}
            else {}
        )

        def chosen_name(path, discovered):
            # Setup keys use installed filenames; built-in names may be plist aliases.
            return next((name for name in scoped if snapshot.index.find(name) == path), discovered)

        for name in config.apps.all:
            p = app_path(name)
            if p:
                if app_running(p) and p not in seen:
                    name = chosen_name(p, name)
                    rule = identify(name=name, bundle_id=app_bid(p))
                    running.append(
                        {
                            "name": name,
                            "path": p,
                            "cpu": snapshot.cpu_for(name, p),
                            "defact": app_action(name, rule, config),
                            **metadata(rule),
                        }
                    )
                    if rule:
                        running[-1].update(bundle_id=app_bid(p), category_ignored=ignored(rule, config))
                    seen.add(p)
            else:
                hits = [f"{proc.pid} {proc.command}" for proc in snapshot.matching(name)]
                if hits:
                    nobundle.append(
                        t("impl.processes_for_were_found_but_its", v0=name, v1="; ".join(hits[:2])[:90])
                    )
        for p in snapshot.index.paths:
            name = chosen_name(p, Path(p).stem)
            if p in seen or name in config.apps.ignore or not app_running(p):
                continue
            bid = app_bid(p)
            rule = identify(name=name, bundle_id=bid)
            if rule:
                running.append(
                    {
                        "name": name,
                        "path": p,
                        "cpu": snapshot.cpu_for(name, p),
                        "bundle_id": bid,
                        "defact": app_action(name, rule, config),
                        "category_ignored": ignored(rule, config),
                        **metadata(rule),
                    }
                )
                seen.add(p)
        if config.apps.policy == "adaptive":
            from ..app_policy import recommendations

            recommended = {row["path"]: row for row in recommendations(snapshot)}
            for p, row in recommended.items():
                if p not in seen and row["running"] and row["name"] not in config.apps.ignore:
                    rule = identify(name=row["name"], bundle_id=row["bundle_id"])
                    running.append(
                        dict(
                            name=row["name"],
                            path=p,
                            cpu=snapshot.cpu_for(row["name"], p),
                            defact=app_action(row["name"], rule, config),
                            **metadata(rule),
                        )
                    )
                    seen.add(p)
            for app in running:
                row = recommended.get(app["path"])
                if row:
                    app["reason"] = row["reason"]
                    if row["reason"] == "foreground":
                        app["defact"] = "skip"
                    elif (
                        config.apps.explicit(app["name"], config.options.scenario) is None
                        and app["defact"] != "skip"
                    ):
                        scenario = (
                            config.options.scenario if config.options.scenario in {"work", "away"} else "work"
                        )
                        app["defact"] = (
                            config.apps.default
                            if row["reason"] == "freeze" and scenario == "work"
                            else row[scenario]
                        )
        notices = []
        for process in snapshot.processes:
            rule = identify(executable=process.command)
            if rule and rule["action"] == "notice":
                notices.append({"name": rule["name"], "pid": process.pid, **metadata(rule)})
        return {"running": running, "nobundle": nobundle, "notices": notices}


def main_proc_alive(p):
    """app 主进程是否存活 (仅 Contents/MacOS 可执行, 不含 Frameworks 里的 appex/helper)."""
    from ..system.app_snapshot import current_snapshot

    snapshot = current_snapshot()
    if snapshot is not None:
        return bool(snapshot.matching(f"^{re.escape(p)}/Contents/MacOS/"))
    from ..system.process_control import app_processes

    return bool(app_processes(p, main_only=True))

"""按可执行文件和进程身份操作应用，不匹配命令行参数。"""

from __future__ import annotations

import os
import plistlib
import time
from dataclasses import dataclass
from pathlib import Path

from ..actions import journal
from ..failures import CommandError
from ..i18n import t
from .shell import is_dry_run, run_rc


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    stat: str
    started: str
    command: str

    def receipt(self, path):
        return dict(pid=self.pid, started=self.started, command=self.command, path=path)


def process_table() -> list[Process]:
    output, rc = run_rc(["ps", "-axo", "pid=,ppid=,stat=,lstart=,comm="], timeout=10)
    if rc:
        raise CommandError(["ps", "process identities"], rc, output)
    result = []
    for line in output.splitlines():
        fields = line.split(None, 8)
        if len(fields) != 9 or not fields[0].isdigit() or not fields[1].isdigit():
            raise ValueError(t("safety.process_row_invalid", row=repr(line)))
        result.append(Process(int(fields[0]), int(fields[1]), fields[2], " ".join(fields[3:8]), fields[8]))
    return result


def _prefixes(path, whole_bundle=False):
    value = Path(path)
    if not value.is_absolute() or value.suffix != ".app":
        raise ValueError(t("safety.app_path_absolute", path=repr(path)))
    paths = tuple(dict.fromkeys((str(value), str(value.resolve()))))
    if whole_bundle:
        return tuple(value + "/" for value in paths)
    return tuple(
        value + "/Contents/" + part + "/" for value in paths for part in ("MacOS", "Frameworks", "Helpers")
    )


def app_processes(path, *, whole_bundle=False, main_only=False):
    prefixes = _prefixes(path, whole_bundle)
    if main_only:
        prefixes = tuple(prefix for prefix in prefixes if prefix.endswith("/MacOS/"))
    return [process for process in process_table() if process.command.startswith(prefixes)]


def _protected(path, rows):
    resolved = Path(path).resolve()
    terminal_names = {"terminal", "iterm", "iterm2", "warp", "wezterm", "alacritty", "ghostty"}
    if resolved.is_relative_to("/System") or resolved.stem.casefold() in terminal_names:
        raise PermissionError(t("safety.protected_app", path=path))
    info = resolved / "Contents/Info.plist"
    if info.exists():
        with info.open("rb") as handle:
            metadata = plistlib.load(handle)
        if not isinstance(metadata, dict):
            raise ValueError(t("safety.expected_dictionary", path=info))
        bundle_id = metadata.get("CFBundleIdentifier", "")
        if not isinstance(bundle_id, str):
            raise ValueError(t("safety.invalid_bundle_id", path=info))
        if bundle_id.startswith("com.apple.") or bundle_id in {
            "com.googlecode.iterm2",
            "dev.warp.Warp-Stable",
            "com.github.wez.wezterm",
            "org.alacritty",
            "com.mitchellh.ghostty",
        }:
            raise PermissionError(t("safety.protected_app", path=f"{path} ({bundle_id})"))
    parents = {process.pid: process.ppid for process in rows}
    protected = {os.getpid(), os.getppid()}
    parent = os.getppid()
    while parent in parents and parents[parent] not in protected:
        parent = parents[parent]
        protected.add(parent)
    prefixes = _prefixes(path, whole_bundle=True)
    if any(process.pid in protected and process.command.startswith(prefixes) for process in rows):
        raise PermissionError(t("safety.protected_ancestor", path=path))


def _same_process(process, receipt):
    return (
        process.pid == receipt["pid"]
        and process.started == receipt["started"]
        and process.command == receipt["command"]
    )


def _signal(process, signal, *, sudo=False):
    # 查询与信号之间仍有内核级竞态；此检查阻止已观察到的 PID 重用。
    current = next((row for row in process_table() if row.pid == process.pid), None)
    if current is None or not _same_process(current, process.receipt("")):
        return False
    output, rc = run_rc(["kill", signal, str(process.pid)], timeout=10, sudo=sudo)
    if rc:
        raise CommandError(["kill", signal, str(process.pid)], rc, output)
    return True


def force_kill(path, *, whole_bundle=False, sudo=False):
    rows = process_table()
    _protected(path, rows)
    prefixes = _prefixes(path, whole_bundle)
    targets = [process for process in rows if process.command.startswith(prefixes)]
    if targets and journal.active_state() is not None:
        journal.record_change("quit_apps", path)
    for process in targets:
        _signal(process, "-TERM", sudo=sudo)
    if is_dry_run():
        return True
    if targets:
        time.sleep(1)
    return not app_processes(path, whole_bundle=whole_bundle, main_only=not whole_bundle)


def freeze_app(path, state=None, save=None):
    if state is not None:
        with journal.mutation_scope(state, save):
            return freeze_app(path)
    rows = process_table()
    _protected(path, rows)
    prefixes = _prefixes(path)
    targets = [row for row in rows if row.command.startswith(prefixes) and not row.stat.startswith("T")]
    if not targets:
        return False
    current_state = journal.active_state()
    if current_state is not None and "frozen_pids" not in current_state:
        # 旧快照的路径不能在新 PID 收据出现后丢失恢复范围。
        current_state["legacy_frozen_apps"] = list(current_state.get("frozen_apps", []))
        current_state["frozen_pids"] = []
        journal.persist()
    journal.record_change("frozen_apps", path)
    for process in targets:
        journal.record_change("frozen_pids", process.receipt(path))
        _signal(process, "-STOP")
    return True


def thaw_app(path, state=None):
    state = journal.active_state() if state is None else state
    rows = process_table()
    receipts = None if state is None else state.get("frozen_pids")
    legacy = state is not None and path in state.get("legacy_frozen_apps", [])
    if receipts is not None and not legacy:
        selected = [receipt for receipt in receipts if receipt["path"] == path]
        targets = [process for process in rows for receipt in selected if _same_process(process, receipt)]
    else:
        # 旧快照没有 PID 身份；只解冻记录 bundle 的同一可执行文件范围。
        prefixes = _prefixes(path)
        targets = [process for process in rows if process.command.startswith(prefixes)]
    for process in targets:
        if process.stat.startswith("T"):
            _signal(process, "-CONT")
        if receipts is not None:
            receipts[:] = [receipt for receipt in receipts if not _same_process(process, receipt)]
            if journal.active_state() is state:
                journal.persist()
    if receipts is not None:
        # 已退出或身份改变的 PID 不属于本次冻结，不能向替代进程发信号。
        receipts[:] = [receipt for receipt in receipts if receipt["path"] != path]
        if journal.active_state() is state:
            journal.persist()
    if legacy:
        state["legacy_frozen_apps"].remove(path)
        if journal.active_state() is state:
            journal.persist()
    return True

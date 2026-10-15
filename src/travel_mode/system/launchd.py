"""LaunchAgent discovery."""

from __future__ import annotations

import plistlib
import re
import shutil
from fnmatch import fnmatchcase
from pathlib import Path
from xml.parsers.expat import ExpatError

from ..categories import identify, ignored, metadata, protected
from ..categories import selected as category_selected
from ..config import Config, load_config
from ..i18n import t
from .shell import get_runner, run_out, run_rc, subprocess_runner


def launchctl_names() -> list[str]:
    output, rc = run_out(["launchctl", "list"], timeout=20)
    if rc:
        raise RuntimeError(t("json.command_failed", command="launchctl list", rc=rc, detail=output))
    return output.splitlines()


def _loaded(lines: list[str]) -> dict[str, tuple[str, str]]:
    result = {}
    for line in lines:
        parts = line.split()
        if len(parts) >= 3 and (parts[0] == "-" or parts[0].lstrip("-").isdigit()):
            result[parts[2]] = (parts[0], parts[1])
        elif len(parts) >= 1 and parts[-1].startswith("com."):
            result[parts[-1]] = (parts[0] if parts else "-", "")
    return result


def match_services(selector: str, rows: list[dict]) -> list[dict]:
    """按精确 label、文件名、glob、子串、简称词元的优先级匹配。"""
    exact = [row for row in rows if row["label"] == selector]
    if exact:
        return exact
    filenames = [row for row in rows if Path(row["plist_path"]).stem == selector.removesuffix(".plist")]
    if filenames:
        return filenames
    glob = [row for row in rows if fnmatchcase(row["label"], selector)]
    if glob:
        return glob
    lowered = selector.casefold()
    substring = [row for row in rows if lowered in row["label"].casefold()]
    if substring:
        return substring
    tokens = lowered.split("-")
    if len(tokens) > 1 and all(tokens):
        return [row for row in rows if all(token in row["label"].casefold() for token in tokens)]
    return []


def time_machine_info(loaded_labels: set[str] | None = None) -> dict:
    """系统备份只读信息；命令和格式错误必须显式报出。"""
    if loaded_labels is not None and not ({"com.apple.backupd", "com.apple.backupd-helper"} & loaded_labels):
        return {
            "name": "Time Machine",
            "labels": ["com.apple.backupd", "com.apple.backupd-helper"],
            "running": False,
            "available": False,
        }
    if shutil.which("tmutil") is None:
        return {
            "name": "Time Machine",
            "labels": ["com.apple.backupd", "com.apple.backupd-helper"],
            "running": False,
            "available": False,
        }
    output, rc = run_out(["tmutil", "status"], timeout=20)
    if rc:
        raise RuntimeError(t("json.command_failed", command="tmutil status", rc=rc, detail=output))
    match = re.search(r"\bRunning\s*=\s*([01])\s*;", output)
    if not match:
        return {
            "name": "Time Machine",
            "labels": ["com.apple.backupd", "com.apple.backupd-helper"],
            "running": None,
            "available": False,
            "error": t("services.tm_unparseable", output=output.strip()),
        }
    return {
        "name": "Time Machine",
        "labels": ["com.apple.backupd", "com.apple.backupd-helper"],
        "running": match[1] == "1",
        "available": True,
    }


def service_information(scan: dict) -> list[str]:
    lines = [t("services.unmatched", item=row["item"]) for row in scan.get("unmatched", [])]
    invalid = scan.get("invalid", [])
    if invalid:
        lines.append(t("services.invalid_summary", count=len(invalid)))
    tm = scan.get("time_machine")
    if tm is not None:
        lines.extend(
            [
                t(
                    "services.tm_heading",
                    status=(
                        t("services.tm_unknown", reason=tm["error"])
                        if tm.get("error")
                        else t("services.tm_unavailable")
                        if not tm["available"]
                        else t("services.tm_active" if tm["running"] else "services.tm_idle")
                    ),
                ),
                t("services.tm_managed"),
            ]
        )
    return lines


def invalid_plist_details(scan: dict) -> list[str]:
    """去重后的无效 plist 明细，仅供日志面板使用；页面与 check 只显示汇总行。"""
    seen = set()
    details = []
    for row in scan.get("invalid", []):
        key = (row["plist_path"], row["error"])
        if key in seen:
            continue
        seen.add(key)
        details.append(t("services.invalid", path=row["plist_path"], error=row["error"]))
    return details


def agent_directories():
    return (Path.home() / "Library/LaunchAgents", Path("/Library/LaunchAgents"))


def svc_scan(config: Config | None = None, *, directories: tuple[Path, ...] | None = None) -> dict:
    config = config or load_config()
    directories = agent_directories() if directories is None else directories
    loaded = _loaded(launchctl_names())
    rows, invalid = [], []
    seen_labels = set()
    seen_invalid: set[str] = set()
    for plist_path in sorted(path for directory in directories for path in directory.glob("*.plist")):
        try:
            with plist_path.open("rb") as fh:
                plist = plistlib.load(fh)
            label = plist.get("Label")
            if not isinstance(label, str) or not label:
                raise ValueError(t("launchd.missing_label"))
        except (
            OSError,
            plistlib.InvalidFileException,
            ExpatError,
            UnicodeError,
            ValueError,
            TypeError,
        ) as exc:
            if str(plist_path) not in seen_invalid:
                seen_invalid.add(str(plist_path))
                invalid.append({"plist_path": str(plist_path), "error": str(exc)})
            continue
        if label not in loaded or label in seen_labels:
            continue
        seen_labels.add(label)
        pid, _ = loaded[label]
        cpu = ""
        if pid.lstrip("-").isdigit() and int(pid) > 0:
            cpu_out, rc = run_rc(["ps", "-p", pid, "-o", "%cpu="])
            if rc:
                raise RuntimeError(
                    t("json.command_failed", command=f"ps -p {pid} -o %cpu=", rc=rc, detail=cpu_out)
                )
            cpu = cpu_out.strip()
        program = plist.get("Program")
        arguments = plist.get("ProgramArguments", [])
        executable = (
            program
            if isinstance(program, str)
            else (
                arguments[0]
                if isinstance(arguments, list) and arguments and isinstance(arguments[0], str)
                else ""
            )
        )
        rule = identify(label=label, executable=executable)
        row = {"label": label, "plist_path": str(plist_path), "name": label, "pid": pid, "cpu": cpu}
        if rule:
            row.update(metadata(rule), executable=executable, category_ignored=ignored(rule, config))
        rows.append(row)
    selected = {"unload": set(), "keep": set()}
    unmatched = []
    seen_unmatched: set[tuple[str, str]] = set()
    for group in selected:
        for item in getattr(config.services, group):
            matches = match_services(item, rows)
            if not matches and (group, item) not in seen_unmatched:
                seen_unmatched.add((group, item))
                unmatched.append({"group": group, "item": item})
            selected[group].update(row["label"] for row in matches)
    for row in rows:
        rule = identify(label=row["label"], executable=row.get("executable", ""))
        if rule and protected(rule, config):
            selected["keep"].add(row["label"])
        if rule and category_selected(rule, config):
            selected["unload"].add(row["label"])
    # keep 优先：重叠的通配配置也不能误卸载；所有 Apple 服务均不提供操作。
    eligible = [row for row in rows if not row["label"].startswith("com.apple.")]
    kept = [row["label"] for row in eligible if row["label"] in selected["keep"]]
    unloadable = [row for row in eligible if row["label"] in selected["unload"] and row["label"] not in kept]
    candidates = [row for row in eligible if row not in unloadable and row["label"] not in kept]
    host_read = get_runner() is subprocess_runner or getattr(get_runner(), "readonly", False)
    tm = time_machine_info(None) if host_read else time_machine_info(set())
    notices = [
        {"label": label, **metadata(rule)}
        for label in sorted(loaded)
        if (rule := identify(label=label)) and rule["action"] == "notice"
    ]
    return {
        "recognized": rows,
        "notices": notices,
        "unloadable": unloadable,
        "candidates": candidates,
        "kept": kept,
        "invalid": invalid,
        "unmatched": unmatched,
        "time_machine": tm,
    }


__all__ = ["launchctl_names", "svc_scan"]

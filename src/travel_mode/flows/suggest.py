"""只读采样发现；确认后用 tomlkit 原样保留配置注释。"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import tomlkit

from .. import __version__
from ..config import load_config
from ..constants import DEFAULT_ACTIONS
from ..i18n import t
from ..paths import config_path
from ..structured import read_command
from ..system.daemon_discovery import discover_daemon_apps
from ..system.launchd import svc_scan
from ..system.pmset import sleep_blocking_assertions
from ..system.shell import run_rc

KNOWN_ELECTRON_OR_IM = {
    "Slack",
    "Discord",
    "Microsoft Teams",
    "Telegram",
    "WhatsApp",
    "WeChat",
    "QQ",
    "DingTalk",
    "Lark",
    "Notion",
    "Obsidian",
    "ChatGPT",
    "Claude",
}


def bundle_for(pid: str) -> str | None:
    output, rc = run_rc(["ps", "-p", pid, "-o", "comm="])
    if rc == 1:
        print(t("suggest.process_exited", pid=pid), file=sys.stderr)
        return None
    if rc:
        raise RuntimeError(t("json.command_failed", command=f"ps -p {pid}", rc=rc, detail=output))
    path = output.strip()
    if ".app/" not in path:
        return None
    return path.split(".app/", 1)[0] + ".app"


def suggest_data(samples: int = 3) -> dict:
    if samples < 1:
        raise ValueError(t("suggest.invalid_samples"))
    config = load_config()
    output = read_command(
        ["top", "-l", str(samples), "-stats", "pid,power,command", "-o", "power"],
        timeout=max(30, samples * 5),
    )
    scores: dict[str, float] = defaultdict(float)
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and int(parts[0]) > 0:
            scores[parts[0]] += float(parts[1].rstrip("+-")) / samples
    assertions = [
        row
        for row in sleep_blocking_assertions(
            read_command(["pmset", "-g", "assertions"]).splitlines(), config.apps.with_daemon
        )
        if row["pid"] and row["pid"].isdigit() and int(row["pid"]) > 0
    ]
    pids = sorted((set(scores) | {row["pid"] for row in assertions}) - {"0"}, key=int)
    pid_bundles = {pid: bundle_for(pid) for pid in pids}
    for assertion in assertions:
        assertion["bundle_path"] = pid_bundles[assertion["pid"]]
    daemon_apps, daemon_warnings = discover_daemon_apps(assertion_rows=assertions)
    daemon_apps = [row for row in daemon_apps if row["name"] not in config.apps.ignore]
    configured_daemons = {item.name for item in config.apps.with_daemon}
    for row in daemon_apps:
        row["configured"] = row["name"] in configured_daemons
    with_daemon = []
    for row in daemon_apps:
        if not row["recommended"] or row["configured"]:
            continue
        with_daemon.append(
            {
                "name": row["name"],
                "group": "apps",
                **{
                    kind: sorted({match["plist_path"] for match in row["matches"] if match["kind"] == kind})
                    for kind in ("system_daemons", "user_agents")
                },
            }
        )
    # 同名 bundle 无法由当前配置 schema 区分，保留候选但不生成含歧义的配置。
    duplicate_names = {
        row["name"] for row in daemon_apps if sum(other["name"] == row["name"] for other in daemon_apps) > 1
    }
    with_daemon = [row for row in with_daemon if row["name"] not in duplicate_names]
    for row in daemon_apps:
        if row["name"] in duplicate_names:
            row["recommended"] = False
    holders = {row["pid"] for row in assertions}
    configured = set(config.apps.actions) | set(config.apps.watch) | set(config.apps.ignore)
    configured.update(item.name for item in config.apps.with_daemon)
    bundles: dict[str, dict] = {}
    for pid in sorted(set(scores) | holders, key=int):
        if scores.get(pid, 0) <= 0 and pid not in holders:
            continue
        path = pid_bundles[pid]
        if path is None:
            continue
        name = Path(path).stem
        if name in configured:
            continue
        row = bundles.setdefault(
            path,
            {
                "name": name,
                "path": path,
                "power": 0.0,
                "action": (
                    "freeze" if name in DEFAULT_ACTIONS["freeze"] or name in KNOWN_ELECTRON_OR_IM else "ask"
                ),
                "pids": [],
            },
        )
        row["power"] += scores.get(pid, 0)
        row["pids"].append(int(pid))
        if pid in holders:
            row["action"] = "quit"
    return {
        "version": __version__.split(".dev", 1)[0],
        "samples": samples,
        "apps": sorted(bundles.values(), key=lambda row: (-row["power"], row["name"])),
        "assertions": assertions,
        "services": svc_scan(config)["candidates"],
        "daemon_apps": daemon_apps,
        "with_daemon": with_daemon,
        "toml": daemon_toml(with_daemon),
        "warnings": daemon_warnings,
    }


def daemon_toml(rows: list[dict]) -> str:
    doc = tomlkit.document()
    if rows:
        entries = tomlkit.aot()
        for row in rows:
            entry = tomlkit.table()
            for key in ("name", "system_daemons", "user_agents", "group"):
                entry.add(key, row[key])
            entries.append(entry)
        table = tomlkit.table(is_super_table=True)
        table.add("with_daemon", entries)
        doc.add("apps", table)
    return tomlkit.dumps(doc)


def write_selection(
    apps: list[dict], services: list[dict], path: Path | None = None, daemon_apps: list[dict] | None = None
):
    target = path or config_path()
    config = load_config(target)
    doc = tomlkit.parse(target.read_text("utf-8")) if target.exists() else tomlkit.document()
    daemon_apps = daemon_apps or []
    original = tomlkit.dumps(doc)
    if apps:
        if "apps" not in doc:
            doc["apps"] = tomlkit.table()
        for row in apps:
            name, action = row["name"], row["action"]
            if action not in {"quit", "freeze", "ask"}:
                raise ValueError(t("config.invalid_action"))
            if name in config.apps.actions or name in config.apps.ignore:
                raise ValueError(t("suggest.already_configured", name=name))
            table = doc["apps"]
            for key in ("watch", action):
                if key not in table:
                    table[key] = tomlkit.array()
                if name not in table[key]:
                    table[key].append(name)
    if services:
        if "services" not in doc:
            doc["services"] = tomlkit.table()
        table = doc["services"]
        if "unload" not in table:
            table["unload"] = tomlkit.array()
        for row in services:
            if row["label"] in config.services.keep:
                raise ValueError(t("suggest.already_configured", name=row["label"]))
            if row["label"] not in table["unload"]:
                table["unload"].append(row["label"])
    if daemon_apps:
        if "apps" not in doc:
            doc["apps"] = tomlkit.table()
        existing = {item.name for item in config.apps.with_daemon}
        for row in daemon_apps:
            if row["name"] in existing:
                print(t("suggest.already_configured", name=row["name"]), file=sys.stderr)
                continue
            if row.get("recommended") is False:
                continue
            entry = tomlkit.table()
            entry.add("name", row["name"])
            entry.add("system_daemons", tomlkit.array(row["system_daemons"]))
            entry.add("user_agents", tomlkit.array(row["user_agents"]))
            entry.add("group", row.get("group", "apps"))
            if "with_daemon" not in doc["apps"]:
                doc["apps"].add("with_daemon", tomlkit.aot())
            doc["apps"]["with_daemon"].append(entry)
            existing.add(row["name"])
    if tomlkit.dumps(doc) == original:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".suggest.tmp")
    try:
        tmp.write_text(tomlkit.dumps(doc), "utf-8")
        load_config(tmp)  # 完整校验成功后才替换原文件。
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def confirm_write(data: dict):
    if not sys.stdin.isatty():
        raise RuntimeError(t("suggest.interactive_required"))
    apps, services, daemon_apps = [], [], []
    for collection, selected in (
        (data["apps"], apps),
        (data["services"], services),
        (data["with_daemon"], daemon_apps),
    ):
        for row in collection:
            name = row.get("name", row.get("label"))
            print(
                t(
                    "suggest.confirm",
                    name=name,
                    action=(
                        t("suggest.daemon_action") if selected is daemon_apps else row.get("action", "unload")
                    ),
                ),
                file=sys.stderr,
                end="",
            )
            answer = input().strip().lower()
            if answer in {"y", "yes"}:
                selected.append(row)
    if (apps or services or daemon_apps) and write_selection(apps, services, daemon_apps=daemon_apps):
        print(t("suggest.written", path=config_path()), file=sys.stderr)


def print_suggestions(data: dict):
    print(t("suggest.heading", samples=data["samples"]))
    for row in data["apps"]:
        print(t("suggest.app", name=row["name"], power=f"{row['power']:.2f}", action=row["action"]))
    print(t("suggest.assertions"))
    for row in data["assertions"]:
        print(row["line"])
    print(t("suggest.services"))
    for row in data["services"]:
        print(row["label"])
    print(t("suggest.daemon_apps"))
    for row in data["daemon_apps"]:
        print(
            t(
                "suggest.daemon_app",
                name=row["name"],
                confidence=row["confidence"],
                match="; ".join(match["evidence"] for match in row["matches"]),
                active=(
                    t("suggest.active_yes") if row["running"] or row["assertion"] else t("suggest.active_no")
                ),
            )
        )
        if row["configured"]:
            print(t("suggest.already_configured", name=row["name"]))
        else:
            print(t("suggest.recommended") if row["recommended"] else t("suggest.review_only"))
        for match in row["matches"]:
            print(f"  {match['plist_path']} ({match['confidence']})")
    if data["toml"]:
        print(t("suggest.toml_heading"))
        print(data["toml"], end="")


def print_warnings(data: dict):
    for warning in data["warnings"]:
        print(t("suggest.warning", path=warning["path"], error=warning["error"]), file=sys.stderr)

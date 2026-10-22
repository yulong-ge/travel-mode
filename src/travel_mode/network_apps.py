"""配置驱动的网络应用清单；进程快照包含主程序和常驻 helper。"""

from __future__ import annotations

from pathlib import Path

from .categories import identify
from .config import Config, load_config
from .i18n import t
from .system.app_snapshot import snapshot_scope


def network_names(config: Config) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (*config.network.apps, *(s.name for s in config.apps.with_daemon if s.group == "network"))
        )
    )


def network_scan(config: Config | None = None) -> list[dict]:
    from .system.apps import app_bid, app_path

    config = config or load_config()
    with snapshot_scope() as snapshot:
        rows = []
        detected = []
        for path in snapshot.index.paths:
            rule = identify(name=Path(path).stem, bundle_id=app_bid(path))
            if rule and rule["category"] == "network":
                detected.append(Path(path).stem)
        for name in dict.fromkeys((*network_names(config), *detected)):
            path = app_path(name)
            paths = snapshot.index.matching_paths(name, path)
            processes = [
                p for p in snapshot.processes if any(p.command.startswith(value + "/") for value in paths)
            ]
            running = bool(processes)
            status = "running" if running else "not_running" if path else "missing"
            if running and not any("/Contents/MacOS/" in p.command for p in processes):
                status = "background"
            rows.append(
                {
                    "name": name,
                    "path": path,
                    "running": running,
                    "status": status,
                    "cpu": sum(float(p.cpu) for p in processes) if running else None,
                    "resident": status == "background",
                }
            )
        # Standalone, sourced helpers can be observed without an application bundle.
        # An absent bundle never becomes an inferred mutation target.
        helpers = {}
        for process in snapshot.processes:
            rule = identify(executable=process.command.split(" ", 1)[0])
            if not rule or rule["category"] != "network":
                continue
            if any(row["path"] and process.command.startswith(row["path"] + "/") for row in rows):
                continue
            helpers.setdefault(rule["id"], (rule, []))[1].append(process)
        for rule, processes in helpers.values():
            rows.append(
                dict(
                    name=rule["name"],
                    path="",
                    running=True,
                    status="background",
                    resident=True,
                    cpu=sum(float(p.cpu) for p in processes),
                    rule_id=rule["id"],
                )
            )
        return rows


def network_description(row: dict) -> str:
    description = t("network.status." + row["status"])
    if row["cpu"] is not None:
        description += f" · CPU {row['cpu']:.1f}%"
    if row["resident"]:
        description += " · " + t("network.resident")
    return f"{row['name']}: {description}"


def configured_names():
    return network_names(load_config())


def disconnect_names(config, observed):
    """Reuse the CLI's observed app scan; never turn a registry row into a target by itself."""
    from .system.process_control import protected_app_identity

    detected = (
        row["name"]
        for row in observed
        if row.get("category") == "network"
        and not row.get("category_ignored")
        and row.get("reason") != "foreground"
        and row["name"] not in config.apps.ignore
        and not protected_app_identity(row["path"], row.get("bundle_id", ""))
    )
    return tuple(dict.fromkeys((*network_names(config), *detected)))

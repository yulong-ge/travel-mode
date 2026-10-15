"""配置驱动的网络应用清单；进程快照包含主程序和常驻 helper。"""

from __future__ import annotations

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
    from .system.apps import app_path

    config = config or load_config()
    with snapshot_scope() as snapshot:
        rows = []
        for name in network_names(config):
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

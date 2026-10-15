"""只读匹配 launchd plist 与已安装 app；候选和可写建议分开。"""

from __future__ import annotations

import os
import plistlib
from pathlib import Path
from xml.parsers.expat import ExpatError

from ..i18n import t
from ..structured import read_command

CONFIDENCE = {"path": 3, "associated": 2, "bundle-id-prefix": 1}


def plist_directories() -> tuple[Path, ...]:
    return (
        Path("/Library/LaunchDaemons"),
        Path("/Library/LaunchAgents"),
        Path.home() / "Library/LaunchAgents",
    )


def app_directories() -> tuple[Path, ...]:
    return (Path("/Applications"), Path.home() / "Applications", Path("/System/Applications"))


def _read_plist(path: Path, warnings: list[dict]) -> dict | None:
    try:
        with path.open("rb") as handle:
            value = plistlib.load(handle)
        if not isinstance(value, dict):
            raise ValueError(t("suggest.plist_not_dictionary"))
        return value
    except (OSError, plistlib.InvalidFileException, ExpatError, ValueError, TypeError, UnicodeError) as exc:
        warnings.append({"path": str(path), "error": str(exc)})
        return None


def _walk_error(error: OSError) -> None:
    raise error


def _installed_apps(roots: tuple[Path, ...], warnings: list[dict]) -> list[dict]:
    apps = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        # 不进入 app 的内部 Helpers/Frameworks，防止把内嵌子程序当作独立 app。
        for directory, children, _ in os.walk(root, onerror=_walk_error):
            path = Path(directory)
            if path.suffix != ".app":
                continue
            children[:] = []
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            info = _read_plist(path / "Contents/Info.plist", warnings)
            if info is None:
                continue
            bundle_id = info.get("CFBundleIdentifier")
            if not isinstance(bundle_id, str) or not bundle_id:
                warnings.append({"path": str(path), "error": t("suggest.missing_bundle_id")})
                continue
            # 配置 app_path 按 bundle 文件名查找；显示名不能替代配置名称。
            apps.append({"name": path.stem, "path": str(path), "bundle_id": bundle_id})
    return sorted(apps, key=lambda row: row["path"])


def _program_paths(plist: dict) -> list[str]:
    arguments = plist.get("ProgramArguments", [])
    values = arguments if isinstance(arguments, list) else []
    if isinstance(plist.get("Program"), str):
        values = [plist["Program"], *values]
    return [value for value in values if isinstance(value, str) and Path(value).is_absolute()]


def _inside(path: str, bundle: str) -> bool:
    return Path(path).resolve().is_relative_to(Path(bundle).resolve())


def _evidence(plist: dict, path: Path, app: dict) -> tuple[str, str] | None:
    for program in _program_paths(plist):
        if _inside(program, app["path"]):
            return "path", t("suggest.evidence_path", program=program, bundle=app["path"])
    associated = plist.get("AssociatedBundleIdentifiers", [])
    if isinstance(associated, str):
        associated = [associated]
    if isinstance(associated, list) and app["bundle_id"] in associated:
        return "associated", t("suggest.evidence_associated", bundle_id=app["bundle_id"])
    label = plist.get("Label", "")
    for field, value in (("Label", label), ("filename", path.stem)):
        if isinstance(value, str) and value and (app["bundle_id"] in value or value in app["bundle_id"]):
            return "bundle-id-prefix", t(
                "suggest.evidence_bundle_id", field=field, value=value, bundle_id=app["bundle_id"]
            )
    return None


def _has_assertion(assertions: list[dict], app: dict, programs: list[str]) -> bool:
    owners = {app["name"], app["bundle_id"], *(Path(program).name for program in programs)}
    for assertion in assertions:
        bundle = assertion.get("bundle_path")
        if bundle:
            if Path(bundle).resolve() == Path(app["path"]).resolve():
                return True
            continue
        if assertion["process"] in owners:
            return True
    return False


def discover_daemon_apps(
    *,
    plist_dirs: tuple[Path, ...] | None = None,
    app_dirs: tuple[Path, ...] | None = None,
    assertion_rows: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """List every app match; recommend only strong, active, auto-start matches."""
    roots = plist_directories() if plist_dirs is None else plist_dirs
    warnings: list[dict] = []
    apps = _installed_apps(app_directories() if app_dirs is None else app_dirs, warnings)
    processes = read_command(["ps", "-axo", "comm="]).splitlines()
    assertions = [] if assertion_rows is None else assertion_rows
    grouped: dict[str, dict] = {}
    for root in roots:
        for path in sorted(root.glob("*.plist")):
            if path.stem.startswith("com.apple."):
                continue
            plist = _read_plist(path, warnings)
            if plist is None:
                continue
            label = plist.get("Label", path.stem)
            if isinstance(label, str) and label.startswith("com.apple."):
                continue
            matches = [(app, evidence) for app in apps if (evidence := _evidence(plist, path, app))]
            if not matches:
                continue
            highest = max(CONFIDENCE[evidence[0]] for _, evidence in matches)
            strongest = sum(CONFIDENCE[evidence[0]] == highest for _, evidence in matches)
            for app, (confidence, evidence) in matches:
                running = any(
                    process.strip().startswith(app["path"] + "/Contents/MacOS/") for process in processes
                )
                asserted = _has_assertion(assertions, app, _program_paths(plist))
                keep_alive = plist.get("KeepAlive") is True
                run_at_load = plist.get("RunAtLoad") is True
                recommended = (
                    CONFIDENCE[confidence] == highest
                    and strongest == 1
                    and confidence != "bundle-id-prefix"
                    and (keep_alive or run_at_load)
                    and (running or asserted)
                )
                row = grouped.setdefault(
                    app["path"],
                    {
                        **app,
                        "matches": [],
                        "confidence": confidence,
                        "running": running,
                        "assertion": False,
                        "recommended": False,
                    },
                )
                if CONFIDENCE[confidence] > CONFIDENCE[row["confidence"]]:
                    row["confidence"] = confidence
                row["assertion"] |= asserted
                row["recommended"] |= recommended
                row["matches"].append(
                    {
                        "plist_path": str(path),
                        "label": label,
                        "kind": "system_daemons" if root.name == "LaunchDaemons" else "user_agents",
                        "confidence": confidence,
                        "evidence": evidence,
                        "keep_alive": keep_alive,
                        "run_at_load": run_at_load,
                        "recommended": recommended,
                    }
                )
    rows = sorted(
        grouped.values(), key=lambda row: (-CONFIDENCE[row["confidence"]], row["name"], row["path"])
    )
    for row in rows:
        row["matches"].sort(key=lambda match: (-CONFIDENCE[match["confidence"]], match["plist_path"]))
        row["system_daemons"] = [
            match["plist_path"] for match in row["matches"] if match["kind"] == "system_daemons"
        ]
        row["user_agents"] = [
            match["plist_path"] for match in row["matches"] if match["kind"] == "user_agents"
        ]
        row["match"] = row["matches"][0]["confidence"]
    return rows, warnings

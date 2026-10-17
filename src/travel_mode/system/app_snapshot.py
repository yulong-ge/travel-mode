"""一次刷新共享应用索引和进程快照；退出作用域后不保留运行态缓存。"""

from __future__ import annotations

import plistlib
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from ..i18n import t
from .shell import run_rc


@dataclass(frozen=True)
class Process:
    pid: str
    cpu: str
    command: str


class AppIndex:
    def __init__(self, directories: list[str]):
        self.exact: dict[str, str] = {}
        self.aliases: dict[str, str] = {}
        self.alias_paths: dict[str, list[str]] = {}
        self.prefixes: list[str] = []
        self.spotlight_paths: list[str] = []
        paths = []
        # 保留目录优先级、一级精确匹配和二级匹配的顺序。
        for directory in directories:
            root = Path(directory)
            direct = sorted(root.glob("*.app"))
            nested = sorted(root.glob("*/*.app"))
            for path in (*direct, *nested):
                if path.is_dir():
                    self.exact.setdefault(path.stem, str(path))
                    paths.append(str(path))
            self.prefixes.extend(str(path) for path in sorted((*direct, *nested)))
        output, rc = run_rc(["mdfind", 'kMDItemContentType == "com.apple.application-bundle"'], timeout=5)
        if rc:
            raise RuntimeError(
                t("json.command_failed", command="mdfind application index", rc=rc, detail=output)
            )
        for line in output.splitlines():
            path = Path(line.strip())
            if path.suffix.lower() == ".app" and path.is_dir():
                paths.append(str(path))
                self.spotlight_paths.append(str(path))
        self.paths = tuple(dict.fromkeys(paths))
        for value in self.paths:
            path = Path(value)
            self._add_alias(path.stem, value)
            plist = path / "Contents/Info.plist"
            try:
                with plist.open("rb") as stream:
                    info = plistlib.load(stream)
            except FileNotFoundError:
                # 无 Info.plist 的 bundle 仍可用文件名匹配（与 app_bid 一致）。
                continue
            except (plistlib.InvalidFileException, ValueError) as exc:
                raise ValueError(f"Cannot index application metadata {plist}: {exc}") from exc
            if not isinstance(info, dict):
                raise ValueError(f"Application metadata must be a dictionary: {plist}")
            for key in ("CFBundleDisplayName", "CFBundleName", "CFBundleIdentifier"):
                alias = info.get(key)
                if isinstance(alias, str) and alias:
                    self._add_alias(alias, value)

    def _add_alias(self, alias: str, path: str) -> None:
        key = alias.casefold()
        self.aliases.setdefault(key, path)
        paths = self.alias_paths.setdefault(key, [])
        for value in (path, str(Path(path).resolve())):
            if value not in paths:
                paths.append(value)

    def matching_paths(self, name: str, primary: str) -> tuple[str, ...]:
        # 同名 bundle 和软链接的两种路径均可出现在 ps 中，无需厂商特例。
        return tuple(dict.fromkeys((primary, *self.alias_paths.get(name.casefold(), ())))) if primary else ()

    def find(self, name: str) -> str:
        if name in self.exact:
            return self.exact[name]
        if name.casefold() in self.aliases:
            return self.aliases[name.casefold()]
        # 原 mdfind -name 是文件名包含匹配，优先于目录前缀兜底。
        for path in self.spotlight_paths:
            if name.casefold() in Path(path).stem.casefold():
                return path
        for path in self.prefixes:
            if Path(path).stem.casefold().startswith(name.casefold()):
                return path
        return ""


@dataclass(frozen=True)
class AppSnapshot:
    index: AppIndex
    processes: tuple[Process, ...]

    @classmethod
    def capture(cls, directories: list[str]) -> AppSnapshot:
        index = AppIndex(directories)
        output, rc = run_rc(["ps", "-axo", "pid=,%cpu=,command="], timeout=5)
        if rc:
            raise RuntimeError(
                t("json.command_failed", command="ps application snapshot", rc=rc, detail=output)
            )
        processes = []
        for line in output.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) != 3 or not parts[0].isdigit():
                raise ValueError(f"Invalid ps application snapshot row: {line!r}")
            try:
                float(parts[1])
            except ValueError as exc:
                raise ValueError(f"Invalid ps CPU in application snapshot: {line!r}") from exc
            processes.append(Process(*parts))
        return cls(index, tuple(processes))

    def matching(self, pattern: str) -> list[Process]:
        return [process for process in self.processes if re.search(pattern, process.command)]

    def cpu_for(self, name: str, path: str) -> str:
        matches = [p for p in self.processes if name in p.command or path + "/" in p.command]
        return max(matches, key=lambda p: float(p.cpu)).cpu if matches else ""


_current: ContextVar[AppSnapshot | None] = ContextVar("app_snapshot", default=None)


def current_snapshot() -> AppSnapshot | None:
    return _current.get()


@contextmanager
def live_process_scope():
    """Mutation verification must not reuse a discovery snapshot."""
    token = _current.set(None)
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def snapshot_scope(snapshot: AppSnapshot | None = None):
    existing = current_snapshot()
    if snapshot is None:
        if existing is not None:
            yield existing
            return
        from .apps import app_directories

        snapshot = AppSnapshot.capture(app_directories())
    token = _current.set(snapshot)
    try:
        yield snapshot
    finally:
        _current.reset(token)

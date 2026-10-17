"""Owned SwiftBar plugin resource and read-only installation inspection."""

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path

from .i18n import t
from .system.shell import ReadError, _read

FILENAME = "travel-mode.5m.sh"
MARKER = b"# travel-mode SwiftBar plugin: io.github.yulong-ge.travel-mode\n"
PREFERENCE = ["defaults", "read", "com.ameba.SwiftBar", "PluginDirectory"]
_preview_directory = ContextVar("menubar_preview_directory", default=None)


class MenubarError(RuntimeError):
    """A plugin operation cannot safely complete."""


def payload():
    return files("travel_mode").joinpath("resources", FILENAME).read_bytes()


def directory():
    if _preview_directory.get() is not None:
        return _preview_directory.get()
    try:
        raw = _read(PREFERENCE, timeout=2).strip()
    except ReadError as exc:
        raise MenubarError(t("menubar.directory_failed", detail=str(exc))) from exc
    path = Path(raw)
    if not raw or not path.is_absolute() or not path.is_dir():
        raise MenubarError(t("menubar.directory_invalid", path=raw))
    return path


@contextmanager
def preview_scope():
    # Resolve the actual preference before command_lifecycle isolates HOME.
    token = _preview_directory.set(directory())
    try:
        yield
    finally:
        _preview_directory.reset(token)


def read_plugin(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise MenubarError(t("menubar.foreign", path=path))
    try:
        return path.read_bytes() if path.exists() else None
    except OSError as exc:
        raise MenubarError(t("menubar.file_failed", path=path, detail=str(exc))) from exc


def plugin_version(content):
    match = re.search(rb"<xbar.version>([^<]+)</xbar.version>", content)
    return match[1].decode("ascii") if match else None


def status():
    path = directory() / FILENAME
    content = read_plugin(path)
    return dict(
        path=str(path),
        installed=content is not None,
        current=content == payload(),
        executable=content is not None and os.access(path, os.X_OK),
        installed_version=plugin_version(content) if content is not None else None,
        bundled_version=plugin_version(payload()),
    )

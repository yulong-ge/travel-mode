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
BUNDLE_ID = "com.ameba.SwiftBar"
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


def swiftbar_running():
    """Verify the running main executable and bundle identity, without launching an app."""
    from .system.app_bundle import inferred_path, read_bundle
    from .system.process_control import process_table

    # comm= is an executable path, not argv. Do not match SwiftBar in arguments,
    # bundle directory names, helpers, or an unrelated executable with the same name.
    for process in process_table(timeout=2):
        executable = Path(process.command)
        if "Z" in process.stat or executable.name != "SwiftBar":
            continue
        bundle = inferred_path(process.command)
        if bundle is None or executable.parent != Path(bundle) / "Contents/MacOS":
            continue
        info = read_bundle(bundle)
        if info.get("CFBundleIdentifier") == BUNDLE_ID and info.get("CFBundleExecutable") == executable.name:
            return True
    return False


def runtime_status():
    from .failures import CommandError

    try:
        running = swiftbar_running()
    except (CommandError, ReadError, OSError, ValueError) as exc:
        return dict(running=None, notice=t("menubar.running_unknown", detail=str(exc)))
    return dict(running=running, notice=None if running else t("menubar.not_running"))


def print_runtime_notice():
    result = runtime_status()
    if result["notice"]:
        print(result["notice"])
    if result["running"] is None:
        _observation(result["notice"])


def status(*, include_runtime=True):
    path = directory() / FILENAME
    content = read_plugin(path)
    result = dict(
        path=str(path),
        installed=content is not None,
        current=content == payload(),
        executable=content is not None and os.access(path, os.X_OK),
        installed_version=plugin_version(content) if content is not None else None,
        bundled_version=plugin_version(payload()),
    )
    if include_runtime:
        from .config import load_config
        from .state import load_state

        show = load_config().menubar.show
        result.update(show=show, hidden=show == "session" and load_state() is None)
        result.update(runtime_status())
        if result["running"] is None:
            _observation(result["notice"])
    return result


REFRESH = ["open", "-g", "swiftbar://refreshplugin?name=travel-mode"]
_refresh_scope = ContextVar("menubar_status_change_scope", default=None)


def _observation(error, commands=()):
    from .failures import record

    record(None, t("menubar.refresh"), str(error), commands, kind="observation", store=False, modifying=False)


def refresh(*, preview=False):
    """One optional notification; it never changes action results or session counts."""
    from .system.shell import run_result

    try:
        installed = status(include_runtime=False)["installed"]
    except (MenubarError, OSError) as exc:
        _observation(exc)
        return
    if not installed:
        return
    runtime = runtime_status()
    if runtime["running"] is not True:
        _observation(t("menubar.refresh_skipped", reason=runtime["notice"]))
        return
    if preview:
        import shlex

        print("[dry-run] " + shlex.join(REFRESH))
        return
    try:
        result = run_result(REFRESH, timeout=2)
    except (OSError, ReadError) as exc:
        _observation(exc)
        return
    if result.rc:
        _observation(
            result.stderr or result.stdout or f"open: rc={result.rc}",
            [dict(command=REFRESH, rc=result.rc, stdout=result.stdout, stderr=result.stderr)],
        )


def status_changed():
    requested = _refresh_scope.get()
    if requested is not None:
        requested[0] = True


def _status_marker():
    from .state import _path

    path = _path()
    result = []
    for target in (path, path.parent / "sleep-readiness.json"):
        try:
            stat = target.stat()
        except FileNotFoundError:
            result.append(None)
        else:
            result.append((stat.st_ino, stat.st_mtime_ns, stat.st_size))
    return tuple(result)


@contextmanager
def status_changes(*, preview=False, deferred=False):
    """Coalesce a CLI operation or TUI transaction, including nested restoration."""
    if _refresh_scope.get() is not None:
        yield None
        return
    from .system.shell import is_dry_run

    preview = preview or is_dry_run()
    before = _status_marker()
    requested = [False]
    token = _refresh_scope.set(requested)

    def finish():
        if requested[0] or preview or before != _status_marker():
            refresh(preview=preview)

    try:
        yield finish
    finally:
        try:
            if not deferred:
                finish()
        finally:
            _refresh_scope.reset(token)

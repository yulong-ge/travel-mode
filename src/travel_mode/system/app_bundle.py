"""Validate macOS application bundles before admitting discovery candidates.

Apple Bundle Programming Guide, Bundle Structures (macOS Contents layout):
https://developer.apple.com/library/archive/documentation/CoreFoundation/Conceptual/CFBundles/BundleTypes/BundleTypes.html
"""

from __future__ import annotations

import os
import plistlib
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from xml.parsers.expat import ExpatError

from ..i18n import t


class InvalidBundle(ValueError):
    """One discovery candidate is not a usable macOS application bundle."""


_observations = ContextVar("bundle_observations", default=None)


@contextmanager
def observation_scope():
    """Deduplicate skipped candidates within a command, without caching validity."""
    current = _observations.get()
    if current is not None:
        yield current
        return
    rows = {}
    token = _observations.set(rows)
    try:
        yield rows
    finally:
        _observations.reset(token)


def read_bundle(value):
    path = Path(value)
    try:
        if not path.is_absolute() or path.suffix.casefold() != ".app" or not path.is_dir():
            raise ValueError(t("apps.bundle_directory"))
        with (path / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        if not isinstance(info, dict):
            raise ValueError(t("suggest.plist_not_dictionary"))
        package = info.get("CFBundlePackageType")
        if package is not None and package != "APPL":
            raise ValueError(t("apps.bundle_type", value=repr(package)))
        if package is None:
            executable = info.get("CFBundleExecutable")
            if (
                not isinstance(executable, str)
                or not executable
                or Path(executable).name != executable
                or executable in {".", ".."}
            ):
                raise ValueError(t("apps.bundle_executable"))
            target = path / "Contents/MacOS" / executable
            if not target.is_file() or not os.access(target, os.X_OK):
                raise ValueError(t("apps.bundle_executable"))
        identifier = info.get("CFBundleIdentifier", "")
        if not isinstance(identifier, str):
            raise ValueError(t("safety.invalid_bundle_id", path=path / "Contents/Info.plist"))
        return info
    except (OSError, plistlib.InvalidFileException, ExpatError, ValueError, TypeError, UnicodeError) as exc:
        raise InvalidBundle(t("apps.bundle_invalid", path=path, reason=exc)) from exc


def discover_bundle(value, warnings=None):
    """Only per-entry metadata failures are skipped; commands never run here."""
    try:
        return read_bundle(value)
    except InvalidBundle as exc:
        row = dict(path=str(value), error=str(exc))
        if warnings is not None and row not in warnings:
            warnings.append(row)
        observations = _observations.get()
        key = (str(value), str(exc))
        if observations is None or key not in observations:
            from ..failures import record

            record(None, t("apps.discovery"), str(exc), kind="observation", target=str(value), store=False)
            if observations is not None:
                observations[key] = row
        return None


def inferred_path(executable):
    match = re.match(r"(.+?\.app)/Contents/", executable, re.IGNORECASE)
    return match[1] if match else None


def bundle_for_executable(executable):
    path = inferred_path(executable)
    return path if path and discover_bundle(path) is not None else None

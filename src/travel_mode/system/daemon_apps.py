"""Configured daemon shutdown with exact labels and durable recovery intent."""

from __future__ import annotations

import plistlib
from pathlib import Path
from xml.parsers.expat import ExpatError

from ..actions.journal import active_state, mutation_scope, persist, record_change
from ..config import DaemonApp
from ..failures import CommandError
from ..presentation import action_message as t
from .apps import app_path, bundle_alive, force_kill, grace_quit
from .launchd import _loaded
from .process_control import force_kill as stop_bundle
from .shell import ReadError, SudoReadError, is_dry_run, record_unknown, run_rc


def _configured_plist(value):
    try:
        return _read_configured_plist(value)
    except PermissionError:
        raise
    except (OSError, ValueError, ExpatError, UnicodeError, TypeError) as exc:
        raise ReadError(t("services.configured_invalid", path=value, reason=exc)) from exc


def _read_configured_plist(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(t("safety.plist_absolute", path=repr(value)))
    with path.open("rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        raise ValueError(t("safety.expected_dictionary", path=path))
    label = plist.get("Label")
    if not isinstance(label, str) or not label:
        raise ValueError(t("safety.missing_label", path=path))
    if label.startswith("com.apple."):
        raise PermissionError(t("safety.protected_service", label=label, path=path))
    return str(path), label


def _loaded_labels(sudo=False):
    output, rc = run_rc(["launchctl", "list"], sudo=sudo, timeout=20)
    if rc:
        raise CommandError((["sudo"] if sudo else []) + ["launchctl", "list"], rc, output)
    return set(_loaded(output.splitlines()))


def quit_with_daemons(spec: DaemonApp, state: dict, save=None):
    if active_state() is not state:
        with mutation_scope(state, save):
            return quit_with_daemons(spec, state, save)
    # 先校验所有配置，避免第二个无效 plist 在第一项已卸载后才被发现。
    configured = (
        (True, "unloaded_sys_svcs", [_configured_plist(value) for value in spec.system_daemons]),
        (False, "unloaded_svcs", [_configured_plist(value) for value in spec.user_agents]),
    )
    messages = []
    ok = True
    uncertain = False
    for sudo, key, plists in configured:
        if not plists:
            continue
        try:
            loaded = _loaded_labels(sudo)
        except SudoReadError as exc:
            if not is_dry_run():
                raise
            error = SudoReadError(
                t("sudo.unknown_item", item=", ".join(label for _, label in plists), detail=str(exc))
            )
            record_unknown(error)
            messages.append(str(error))
            ok = False
            uncertain = True
            continue
        for path, label in plists:
            if label not in loaded:
                continue
            record_change(key, path)
            output, rc = run_rc(["launchctl", "unload", path], sudo=sudo, timeout=20)
            if rc == 0:
                persist()
                messages.append(
                    t(
                        "daemon_apps.system_daemon_unloaded" if sudo else "daemon_apps.user_agent_unloaded",
                        v0=path,
                    )
                )
            else:
                ok = False
                messages.append(
                    t(
                        "daemon_apps.unloading_system_daemon_failed"
                        if sudo
                        else "daemon_apps.unloading_user_agent_failed",
                        v0=path,
                    )
                    + f" (rc={rc}): {output}"
                )
    if uncertain:
        # Do not infer whether an app whose system daemons were unreadable can quit.
        return "", False, "; ".join(messages)
    path = app_path(spec.name)
    if path and bundle_alive(path):
        record_change("quit_apps", path)
        result = grace_quit(path, spec.name)
        if result and getattr(result, "helpers_remaining", False):
            messages.append(result.explanation(spec.name))
            return path, ok, "; ".join(messages)
        if is_dry_run() and result:
            # A planned Quit cannot change the live process table. Do not turn
            # that unchanged observation into a failure or extra force signals.
            messages.append(t("daemon_apps.quit_off_restores_it", v0=spec.name))
            return path, ok, "; ".join(messages)
        if not result:
            from ..config import load_config

            message = (
                result.explanation(spec.name) if hasattr(result, "explanation") else t("failure.incomplete")
            )
            if load_config().options.scenario == "work":
                messages.append(message)
                return path, False, "; ".join(messages)
        normal_detail = (
            getattr(result, "stderr", "") or getattr(result, "stdout", "") or t("failure.incomplete")
        )
        try:
            if bundle_alive(path):
                if spec.system_daemons:
                    stop_bundle(path, sudo=True)
                else:
                    force_kill(path)
            if bundle_alive(path):
                stop_bundle(path, whole_bundle=True, sudo=bool(spec.system_daemons))
        except CommandError as exc:
            return (
                path,
                False,
                t("failure.quit_steps_failed", name=spec.name, normal=normal_detail, force=str(exc)),
            )
        if bundle_alive(path):
            return (
                path,
                False,
                t(
                    "failure.quit_steps_failed",
                    name=spec.name,
                    normal=normal_detail,
                    force=t("daemon_apps.remaining_processes", v0=spec.name),
                ),
            )
        persist()
        if result:
            messages.append(t("daemon_apps.quit_off_restores_it", v0=spec.name))
    return path, ok, "; ".join(messages)


__all__ = ["quit_with_daemons"]


def quit_with_configured_daemon(state, save=None):
    from ..config import load_config

    daemons = [item for item in load_config().apps.with_daemon if item.group == "network"]
    if not daemons:
        return "", True, t("impl.no_daemon_app_is_configured")
    path, ok, messages = "", True, []
    for spec in daemons:
        path, succeeded, message = quit_with_daemons(spec, state, save)
        ok = ok and succeeded
        messages.append(message)
    return path, ok, "; ".join(messages)

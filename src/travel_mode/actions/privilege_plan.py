"""Finite privilege manifests shared by admission and recovery presentation."""

from pathlib import Path

from ..i18n import t


def action_requests(actions):
    requests = []
    for action in actions:
        if getattr(action, "sudo", False) and callable(getattr(action, "command", None)):
            command = action.command()
            key = (
                "enable_sleep"
                if command[:3] == ["pmset", "-a", "disablesleep"]
                else "apply_power"
                if command[:1] == ["pmset"]
                else "restore_spotlight"
                if command[:3] == ["mdutil", "-i", "on"]
                else "pause_spotlight"
                if command[:3] == ["mdutil", "-i", "off"]
                else "disable_wifi"
                if command[:2] == ["networksetup", "-setairportpower"]
                else None
            )
            requests.append((command, t("admin." + key) if key else action.describe()))
        specs = [action.spec] if getattr(action, "spec", None) else []
        if type(action).__name__ == "QuitConfiguredDaemons":
            from ..config import load_config

            specs = [spec for spec in load_config().apps.with_daemon if spec.group == "network"]
        for spec in specs:
            if not spec.system_daemons:
                continue
            requests.append((["launchctl", "list"], spec.name))
            requests.extend((["launchctl", "unload", path], spec.name) for path in spec.system_daemons)
            from ..system import apps, process_control

            path = apps.app_path(spec.name)
            if path:
                for process in process_control.app_processes(path, whole_bundle=True):
                    requests.append((["kill", "-TERM", str(process.pid)], spec.name))
    return list({tuple(command): (command, name) for command, name in requests}.values())


def restore_requests(state, only=None):
    from ..system.pmset import _profile_args

    requests = []
    for key, flag in (("batt_prof", "-b"), ("ac_prof", "-c")):
        if state.get(key) and (only is None or key in only):
            args = _profile_args(state[key])
            if args:
                requests.append((["pmset", flag, *args], restore_name(key)))
    for key, command in (
        ("sleepdisabled", ["pmset", "-a", "disablesleep", state.get("sleepdisabled")]),
        ("spotlight_off", ["mdutil", "-i", "on", "/"]),
        ("wifi_off", ["networksetup", "-setairportpower", state.get("wifi_off"), "on"]),
    ):
        if state.get(key) and (only is None or key in only):
            requests.append((command, restore_name(key)))
    if state.get("unloaded_sys_svcs") and (only is None or "unloaded_sys_svcs" in only):
        requests.append((["launchctl", "list"], restore_name("unloaded_sys_svcs")))
        requests.extend(
            (["launchctl", "load", path], t("admin.restore_daemon", name=Path(path).stem))
            for path in state["unloaded_sys_svcs"]
        )
    return requests


def restore_name(key, *, pending=False):
    names = dict(
        batt_prof="power_b",
        ac_prof="power_c",
        sleepdisabled="sleep",
        spotlight_off="spotlight",
        wifi_off="wifi",
        bt_off="bluetooth",
        brightness="brightness",
        unloaded_sys_svcs="system_services",
        unloaded_svcs="services",
        quit_apps="apps",
        frozen_apps="frozen_apps",
    )
    return (
        t("admin." + ("item_" if pending else "restore_") + names[key])
        if key in names
        else t("failure.restore")
    )


def pending_restore(state):
    rows = []
    for failure in state.get("action_failures", []):
        if not failure.get("recovered") and failure.get("target", "").startswith("restore:"):
            key = failure["target"].removeprefix("restore:")
            if state.get(key):
                row = dict(
                    key=key,
                    name=restore_name(key, pending=True),
                    reason=t("admin.password_reason")
                    if failure["kind"] == "not_executed"
                    else failure["reason"],
                )
                rows = [previous for previous in rows if previous["key"] != key]
                rows.append(row)
    return rows

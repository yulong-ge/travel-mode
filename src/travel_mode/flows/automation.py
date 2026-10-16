"""One power-source event, bounded commands, no authentication or resident work."""

from __future__ import annotations

import time
from contextlib import contextmanager

from .. import automation, snapshots, state
from ..actions.apps import FreezeApp, QuitApp, UnloadAgent, configured_daemon
from ..actions.automation import Notify, RestoreAutomation
from ..actions.base import Context
from ..actions.executor import Executor
from ..actions.power import SetPowerProfile
from ..config import load_config
from ..i18n import select_language, set_language, t
from ..profiles import profile_scope
from ..system import apps, launchd, shell
from ..system.platform import map_pmset_key, supported_pmset_keys
from ..system.sudo import Credentials, credential_scope


class NonInteractiveCredentials(Credentials):
    def ensure(self, command):
        # The Runner below forces sudo -n for the actual operation. Its exit
        # status is the authorization result; no password/refresh thread exists.
        return None


@contextmanager
def event_commands(*, clock=time.monotonic, seconds=45):
    original = shell.get_runner()
    deadline = clock() + seconds

    def bounded(command, timeout=None, interactive=False):
        remaining = deadline - clock()
        if interactive or remaining <= 0:
            raise automation.AutomationError(t("automation.event_deadline"))
        if command[:1] == ["sudo"] and command[1:2] != ["-n"]:
            command = ["sudo", "-n", *command[1:]]
        return original(command, timeout=min(timeout or 20, remaining), interactive=False)

    shell.set_runner(bounded)
    try:
        with credential_scope(NonInteractiveCredentials()):
            yield
    finally:
        shell.set_runner(original)


def power_source():
    output = shell._read(["pmset", "-g", "ps"], timeout=5)
    for label, source in (("Battery Power", "battery"), ("AC Power", "ac"), ("UPS Power", "ups")):
        if label in output:
            return source
    raise automation.AutomationError(t("automation.unknown_source", detail=output.strip()))


def apply_work_safe(config, details):
    with profile_scope(config, "work", scenario="work"):
        st, merged = snapshots.snapshot_origin()
        if not merged:
            st["scenario"] = "work"
        executor = Executor(Context(st, state.save_state))
        actions = []
        key = map_pmset_key("lowpowermode", supported_pmset_keys())
        original = st.get("power_origin", {}).get("batt_prof", "").split()
        if key is None or key not in original[::2]:
            details.append(t("power.battery_unavailable"))
        else:
            output, rc = shell.run_rc(["sudo", "-n", "-l", "pmset", "-b", key, "1"], timeout=5)
            if rc:
                details.append(t("automation.sudo_skipped", detail=output.strip()))
            else:
                actions.append(SetPowerProfile([key, "1"]))
        actions.extend(
            UnloadAgent(row["plist_path"])
            for row in launchd.svc_scan()["unloadable"]
            if row.get("label") != automation.LABEL
        )
        for row in apps.scan_apps()["running"]:
            name = row["name"]
            if name not in config.apps.configured_actions or row["defact"] not in {"quit", "freeze"}:
                continue
            if configured_daemon(name) is not None:
                details.append(t("automation.daemon_skipped", name=name))
                continue
            actions.append(
                FreezeApp(row["path"]) if row["defact"] == "freeze" else QuitApp(row["path"], name)
            )
        for action in actions:
            if action.risk != "safe":
                raise automation.AutomationError(t("automation.unsafe_action", action=type(action).__name__))
            result = executor.execute(action)
            ok = result[1] == 0 if isinstance(result, tuple) else bool(result)
            if not ok:
                raise automation.AutomationError(t("automation.action_failed", action=action.describe()))
            details.append(action.describe())


def event(*, launch_event=False, now=time.time):
    row = {"at": now(), "source": None, "result": "handled", "details": []}
    retain = 50
    try:
        if launch_event:
            from ..system.events import consume_once

            consume_once()
        config = load_config()
        retain = config.automation.retain
        set_language(select_language(None, config.options.language))
        with event_commands():
            row["source"] = source = power_source()
            previous = next(
                (
                    entry
                    for entry in reversed(automation.read_log()["entries"])
                    if entry["source"] is not None and entry["result"] != "ignored"
                ),
                None,
            )
            if previous is not None and source == previous["source"] and 0 <= row["at"] - previous["at"] < 30:
                row["result"] = "ignored"
                row["details"].append(t("automation.debounced"))
            elif previous is not None and source == previous["source"]:
                row["result"] = "ignored"
                row["details"].append(t("automation.unchanged"))
            elif source == "battery":
                policy = config.automation.on_battery
                if policy == "apply-work-safe":
                    apply_work_safe(config, row["details"])
                if policy != "off":
                    _notify(
                        t("automation.battery_notice")
                        + ("\n" + "\n".join(row["details"]) if row["details"] else ""),
                        row,
                    )
            elif source == "ac":
                policy = config.automation.on_ac
                if state.load_state() is not None:
                    if policy == "off-restore":
                        if not Executor(Context(None)).execute(RestoreAutomation()):
                            raise automation.AutomationError(t("automation.restore_failed"))
                        row["details"].append(t("automation.restored"))
                    if policy != "off":
                        _notify(
                            t("automation.restored")
                            if policy == "off-restore"
                            else t("automation.ac_notice"),
                            row,
                        )
            else:
                row["details"].append(t("automation.ups_ignored"))
    except Exception as exc:
        # Preserve the exception and its chain after adding the event receipt.
        row["result"] = "failed"
        row["details"].append(f"{type(exc).__name__}: {exc}")
        automation.append_log(row, retain)
        raise
    automation.append_log(row, retain)
    return 1 if row["result"] == "failed" else 0


def _notify(message, row):
    output, rc = Executor(Context(None)).execute(Notify(message))
    if rc:
        row["result"] = "failed"
        row["details"].append(t("automation.notification_failed", detail=output.strip(), rc=rc))

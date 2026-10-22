"""Explicit system-sleep repair with durable intent and normal sudo credentials."""

from .. import menubar, state
from ..actions.base import Context
from ..actions.executor import Executor, _sudo_lifecycle
from ..actions.journal import mutation_scope
from ..actions.power import EnableSleep
from ..i18n import t
from ..system.pmset import parse_sleep_disabled
from ..system.shell import ReadError, _read


@_sudo_lifecycle
def repair():
    original = parse_sleep_disabled(_read(["pmset", "-g"], timeout=2))
    if original is None:
        raise ReadError(t("sleep.unavailable"))
    if original == "0":
        menubar.status_changed()
        print(t("sleep.repaired"))
        return 0
    existing = state.load_state()
    snapshot = existing if existing is not None else state.new_state()
    # An uncertain standalone repair may be retried. Recognize its structured
    # intent without mistaking a user's active session for this transaction.
    standalone = existing is None or (
        "session" not in snapshot
        and set(snapshot.get("power_origin", {})) == {"sleepdisabled"}
        and not any(
            snapshot.get(key)
            for key in (
                "quit_apps",
                "frozen_apps",
                "frozen_pids",
                "unloaded_svcs",
                "unloaded_sys_svcs",
                "batt_prof",
                "ac_prof",
                "brightness",
                "wifi_off",
                "bt_off",
                "spotlight_off",
                "profile",
                "scenario",
            )
        )
        and all(
            row.get("identity", {}).get("type") == "EnableSleep"
            for row in snapshot.get("action_failures", [])
        )
        and all(
            command == ["sudo", "pmset", "-a", "disablesleep", "0"]
            for command in snapshot.get("pending_commands", [])
        )
    )
    snapshot.setdefault("power_origin", {}).setdefault("sleepdisabled", original)
    # Standalone repair is a preference correction, not a new saving session.
    # Persist recovery intent before pmset; retain it if the command fails.
    with mutation_scope(snapshot, state.save_state):
        _, rc = Executor(Context(snapshot), count_actions=False).execute(EnableSleep())
    if rc:
        # Password cancellation can fail before any command or recovery intent.
        if standalone and not snapshot.get("pending_commands") and not snapshot.get("sleepdisabled"):
            state._path().unlink(missing_ok=True)
        return 1
    if standalone:
        state._path().unlink()
    else:
        # An explicit repair supersedes an older recovery baseline. Off must not
        # disable sleep again after the user has explicitly repaired this switch.
        snapshot["sleepdisabled"] = ""
        snapshot["power_origin"]["sleepdisabled"] = "0"
        state.save_state(snapshot)
    menubar.status_changed()
    print(t("sleep.repaired"))
    return 0

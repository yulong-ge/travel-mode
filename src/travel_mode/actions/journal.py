"""Write recovery intent before a mutation; never infer success from an absent receipt."""

from contextlib import contextmanager
from contextvars import ContextVar

from .. import state as state_module
from ..i18n import t
from ..state import StateError

_active: ContextVar[tuple[dict, object] | None] = ContextVar("travel_mutation", default=None)


def active_state():
    current = _active.get()
    return current[0] if current else None


@contextmanager
def suspend_mutations():
    token = _active.set(None)
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def mutation_scope(state, save=None):
    token = _active.set((state, save or state_module.save_state))
    try:
        yield state
    finally:
        _active.reset(token)


def persist():
    current = _active.get()
    if current:
        state, save = current
        save(state)


def record_change(key, value):
    state = active_state()
    if state is None:
        raise StateError(t("safety.journal_required", field=key))
    if isinstance(value, dict) or key in {"quit_apps", "frozen_apps", "unloaded_svcs", "unloaded_sys_svcs"}:
        values = state.setdefault(key, [])
        if value not in values:
            values.append(value)
    elif not state.get(key):
        state[key] = value
    persist()


def prepare_command(command):
    """Persist scoped command intent. Failed/time-out commands remain uncertain and recoverable."""
    state = active_state()
    if state is None:
        return False
    sudo = command[0] == "sudo"
    cmd = command[1:] if sudo else command
    if not cmd:
        return False
    if cmd[0] == "pmset" and len(cmd) >= 4 and cmd[1] in {"-b", "-c", "-a"}:
        if cmd[1] == "-a":
            if cmd[2] != "disablesleep" or len(cmd) != 4:
                raise StateError(t("safety.unsupported_pmset", command=cmd))
            original = state.get("sleepdisabled") or state.get("power_origin", {}).get("sleepdisabled")
            if original not in {"0", "1"}:
                raise StateError(t("safety.missing_origin", field="disablesleep / SleepDisabled"))
            record_change("sleepdisabled", original)
        else:
            field = "batt_prof" if cmd[1] == "-b" else "ac_prof"
            original = state.get("power_origin", {}).get(field, state.get(field, ""))
            tokens = original.split()
            originals = dict(zip(tokens[::2], tokens[1::2], strict=True))
            recorded = state.get(field, "").split()
            saved = dict(zip(recorded[::2], recorded[1::2], strict=True))
            for key in cmd[2::2]:
                if key not in originals:
                    raise StateError(t("safety.missing_origin", field=f"{field} / {key}"))
                saved.setdefault(key, originals[key])
            state[field] = " ".join(f"{key} {value}" for key, value in saved.items())
            persist()
    elif cmd[:2] == ["mdutil", "-i"] and cmd[2:] == ["off", "/"]:
        record_change("spotlight_off", "1")
    elif cmd[:2] == ["networksetup", "-setairportpower"] and len(cmd) == 4 and cmd[3] == "off":
        record_change("wifi_off", cmd[2])
    elif cmd == ["blueutil", "-p", "0"]:
        record_change("bt_off", "1")
    elif cmd[:2] == ["launchctl", "unload"] and len(cmd) == 3:
        record_change("unloaded_sys_svcs" if sudo else "unloaded_svcs", cmd[2])
    elif cmd[0] == "brightness" and len(cmd) == 2 and cmd[1] != "-l":
        if not state.get("brightness"):
            from ..system.brightness import current_brightness

            original = current_brightness()
            if not original:
                raise StateError(t("safety.missing_origin", field="brightness"))
            record_change("brightness", original)
    else:
        return False
    pending = state.setdefault("pending_commands", [])
    if command not in pending:
        pending.append(list(command))
    persist()
    return True


def complete_command(command, rc):
    if rc == 0:
        state = active_state()
        if state and command in state.get("pending_commands", []):
            state["pending_commands"].remove(command)
        persist()

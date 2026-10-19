"""Explicit admission of changes to an outstanding recovery session."""

from datetime import datetime

from .constants import LIST_KEYS
from .i18n import t


def describe(state):
    started = state.get("session", {}).get("started_at") or state.get("anchored_at") or state.get("saved_at")
    when = datetime.fromisoformat(started).astimezone() if started else None
    date = (
        t("conflict.date", month=when.month, day=when.day, hour=when.hour, minute=when.minute)
        if when
        else t("session.unknown")
    )
    count = sum(len(state.get(key, [])) for key in LIST_KEYS)
    count += sum(
        bool(state.get(key))
        for key in (
            "batt_prof",
            "ac_prof",
            "sleepdisabled",
            "brightness",
            "wifi_off",
            "bt_off",
            "spotlight_off",
        )
    )
    return t(
        "conflict.existing",
        scenario=t("conflict.scene_" + (state.get("scenario") or "away")),
        date=date,
        count=count,
    )


def resolve(state, scenario, *, choice, restore):
    if choice == "cancel":
        return False
    if choice == "restore":
        return bool(restore())
    if choice != "continue":
        raise ValueError(f"Invalid session choice: {choice}")
    if scenario != state.get("scenario") and scenario in {"work", "away"}:
        from .sessions import begin

        begin(state)
        if "session" in state:
            rows = state["session"].setdefault("appended_scenarios", [])
            if scenario not in rows:
                rows.append(scenario)
    return True


def restore_active(state, emit):
    from . import sessions
    from .flows.restore import run_restore
    from .state import _path, save_state

    n, failed = run_restore(state, emit)
    if failed:
        if "session" in state:
            state["session"]["restored"] += n
            state["session"]["restore_failures"] += failed
            save_state(state)
        emit("bad", t("impl.restores_failed_snapshot_retained_resolve_failures", v0=failed))
        return False
    sessions.finish(state, n, 0)
    from .system.shell import dry_run_state_dir, is_dry_run

    target = dry_run_state_dir() / "state.json" if is_dry_run() else _path()
    target.unlink(missing_ok=True)
    return True


def choose_cli(state, scenario, *, yes, continue_session, emit):
    emit("warn", describe(state))
    if continue_session:
        choice = "continue"
    elif yes:
        emit("warn", t("conflict.yes_cancel"))
        return False
    else:
        try:
            answer = input(t("conflict.prompt")).strip().lower()
        except EOFError:
            answer = "cancel"
        choice = {"": "restore", "1": "restore", "2": "continue", "3": "cancel"}.get(answer, answer)
        if choice not in {"restore", "continue", "cancel"}:
            emit("warn", t("conflict.invalid"))
            return False
    return resolve(state, scenario, choice=choice, restore=lambda: restore_active(state, emit))

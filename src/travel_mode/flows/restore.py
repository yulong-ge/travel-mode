"""flow_restore implementation."""

from __future__ import annotations

import plistlib
import shlex
import sys
from pathlib import Path

from travel_mode import snapshots
from travel_mode import state as state_api
from travel_mode.actions import runtime
from travel_mode.flows import common
from travel_mode.paths import state_path
from travel_mode.presentation import action_message as t
from travel_mode.system import pmset, shell
from travel_mode.system.shell import is_dry_run, run_rc


def run_restore(st, emit, only=None):
    """Restore only outstanding receipts; checkpoint each successful item."""
    from ..actions.journal import suspend_mutations
    from ..failures import batch, reporter

    with suspend_mutations(), reporter(emit), batch(emit):
        return _run_restore(st, emit, only)


def _run_restore(st, emit, only=None):
    from .. import failures
    from ..actions.apps import ThawApp
    from ..actions.base import Context, RecoveryCommand
    from ..actions.executor import Executor
    from ..actions.power import SetBrightness
    from ..failures import reporter
    from ..system.launchd import _loaded
    from ..system.sudo import SudoCredentialError

    n = fails = 0
    executor = Executor(Context(st, state_api.save_state), count_actions=False)

    def allowed(key):
        return only is None or key in only

    def failed_read(path, error, commands=(), key=None):
        nonlocal fails
        label = t("failure.restore") + ": " + str(path)
        if isinstance(error, SudoCredentialError):
            commands = [*commands, dict(command=error.command, rc=77, stdout="", stderr=error.detail)]
        row = failures.record(
            st,
            label,
            str(error),
            commands,
            target="restore:" + key if key else str(path),
            kind="not_executed" if isinstance(error, SudoCredentialError) else "action",
        )
        state_api.save_state(st)
        failures.result(False, label)
        emit("bad", failures.message(row))
        fails += 1

    def restored(key, value=None):
        nonlocal n
        if value is None:
            st[key] = ""
        else:
            st[key].remove(value)

        # All pending operations are recovery intentions. Clear only this item's
        # intents; keep uncertain failures for the next off.
        def belongs(cmd):
            c = cmd[1:] if cmd[:1] == ["sudo"] else cmd
            if key in {"batt_prof", "ac_prof"}:
                return c[:2] == ["pmset", "-b" if key == "batt_prof" else "-c"]
            if key == "sleepdisabled":
                return c[:3] == ["pmset", "-a", "disablesleep"]
            if key == "unloaded_sys_svcs" or key == "unloaded_svcs":
                return c[:2] == ["launchctl", "unload"] and c[-1] == value
            tool = {
                "spotlight_off": "mdutil",
                "wifi_off": "networksetup",
                "bt_off": "blueutil",
                "brightness": "brightness",
            }.get(key)
            return tool is not None and c[:1] == [tool]

        st["pending_commands"] = [cmd for cmd in st.get("pending_commands", []) if not belongs(cmd)]
        for failure in st.get("action_failures", []):
            if failure.get("target") == "restore:" + key:
                failure["recovered"] = True
        n += 1
        state_api.save_state(st)

    def execute(key, cmd, message, *, sudo=False, value=None):
        nonlocal fails
        with reporter(emit):
            output, rc = executor.execute(RecoveryCommand(cmd, message, sudo, key))
        if rc:
            if rc not in {77, 124}:
                emit("warn", t("json.command_failed", command=shlex.join(cmd), rc=rc, detail=output))
            fails += 1
            return
        restored(key, value)
        emit("ok", message)

    for field, flag, message in (
        ("batt_prof", "-b", "impl.battery_profile_restored"),
        ("ac_prof", "-c", "impl.ac_profile_restored"),
    ):
        profile = st.get(field)
        if not profile or not allowed(field):
            continue
        args = pmset._profile_args(profile)
        if not args:
            emit(
                "warn",
                t(
                    "impl.battery_profile_has_no_keys_supported"
                    if field == "batt_prof"
                    else "impl.ac_profile_has_no_keys_supported"
                ),
            )
            fails += 1
            continue
        execute(field, ["pmset", flag, *args], t(message, v0=profile), sudo=True)
    if st.get("sleepdisabled") and allowed("sleepdisabled"):
        execute(
            "sleepdisabled",
            ["pmset", "-a", "disablesleep", st["sleepdisabled"]],
            f"disablesleep → {st['sleepdisabled']}",
            sudo=True,
        )
    for field, sudo in (("unloaded_sys_svcs", True), ("unloaded_svcs", False)):
        paths = list(st.get(field, []))
        if not paths or not allowed(field):
            continue
        with failures.capture() as commands:
            try:
                loaded = _loaded(shell._read(["launchctl", "list"], sudo=sudo).splitlines())
            except (shell.ReadError, shell.SudoReadError, SudoCredentialError) as exc:
                if is_dry_run() and isinstance(exc, shell.SudoReadError):
                    shell.record_unknown(exc)
                for path in paths:
                    failed_read(path, exc, commands, key=field)
                continue
        for path in paths:
            try:
                with Path(path).open("rb") as fh:
                    data = plistlib.load(fh)
                if not isinstance(data, dict):
                    raise ValueError(t("safety.expected_dictionary", path=path))
                label = data.get("Label")
                if not isinstance(label, str) or not label or label.startswith("com.apple."):
                    raise ValueError(t("safety.invalid_restore_label", path=path, label=repr(label)))
            except (OSError, ValueError, plistlib.InvalidFileException) as exc:
                failed_read(path, exc, key=field)
                continue
            if label in loaded:
                restored(field, path)
                emit("dim", t("impl.agent_already_running_loaded_at_login", v0=label))
            else:
                execute(
                    field,
                    ["launchctl", "load", path],
                    t("impl.reloaded_agent", v0=label),
                    sudo=sudo,
                    value=path,
                )
    if st.get("spotlight_off") and allowed("spotlight_off"):
        execute(
            "spotlight_off", ["mdutil", "-i", "on", "/"], t("impl.spotlight_indexing_restored"), sudo=True
        )
    if st.get("wifi_off") and allowed("wifi_off"):
        execute(
            "wifi_off",
            ["networksetup", "-setairportpower", st["wifi_off"], "on"],
            t("impl.wi_fi_enabled", v0=st["wifi_off"]),
            sudo=True,
        )
    if st.get("bt_off") and allowed("bt_off"):
        execute("bt_off", ["blueutil", "-p", "1"], t("impl.bluetooth_enabled"))
    if st.get("brightness") and allowed("brightness"):
        action = SetBrightness(st["brightness"])
        action.restore_key = "brightness"
        _, rc = executor.execute(action)
        if rc:
            fails += 1
            emit("warn", t("impl.restoring_brightness_failed"))
        else:
            value = st["brightness"]
            restored("brightness")
            emit("ok", t("impl.brightness_restored", v0=value))
    frozen_paths = list(
        dict.fromkeys(
            [*st.get("frozen_apps", []), *(receipt["path"] for receipt in st.get("frozen_pids", []))]
        )
    )
    for path in frozen_paths if allowed("frozen_apps") or allowed("frozen_pids") else ():
        action = ThawApp(path, st)
        action.restore_key = "frozen_apps"
        ok = executor.execute(action)
        if not ok:
            fails += 1
            emit("warn", t("impl.thaw_failed", v0=path))
            state_api.save_state(st)  # 已成功 CONT 的 PID 不得因另一 PID 失败而留在磁盘待恢复列表。
        else:
            if path in st.get("frozen_apps", []):
                st["frozen_apps"].remove(path)
            state_api.save_state(st)
            n += 1
            emit("ok", t("impl.thawed", v0=Path(path).stem))
    for path in list(st.get("quit_apps", [])) if allowed("quit_apps") else ():
        execute(
            "quit_apps",
            ["open", path] if Path(path).is_dir() else ["open", "-a", path],
            t("impl.started", v0=path),
            value=path,
        )
    return n, fails


@runtime._sudo_lifecycle
def mode_off(force_off=False, dry_run=False, *, verbose=False):
    runtime._prepare_dry_run(dry_run)
    c = __import__("rich.console", fromlist=["Console"]).Console()

    def emit(kind, msg):
        mark = {"ok": "[green]✓[/]", "warn": "[yellow]![/]", "bad": "[red]✗[/]", "dim": "[dim]·[/]"}[kind]
        c.print(f"{mark} {msg}")

    st = snapshots.load_state()
    if st is None:
        emit("warn", t("impl.no_snapshot_found_at_cannot_restore", v0=common.STATE_PATH))
        emit("dim", t("impl.use_travel_mode_thaw_to_resume"))
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            runtime._finish_dry_run()
            return 1
        try:
            ans = input(t("impl.run_sudo_pmset_restoredefaults_to_reset")).strip().lower()
        except EOFError:
            ans = ""
        if ans in ("y", "yes"):
            _, rc = run_rc(["pmset", "restoredefaults"], sudo=True)
            emit(
                "ok" if rc == 0 else "bad",
                t("impl.requested_factory_power_defaults") if rc == 0 else t("impl.restoredefaults_failed"),
            )
            runtime._finish_dry_run()
            return 0 if rc == 0 else 1
        runtime._finish_dry_run()
        return 1
    age = snapshots.anchor_age_hours(st)
    if age is not None and age > 24:
        emit("warn", t("impl.snapshot_is_hours_old_off_rolls", v0=age))
    emit("ok", t("impl.restoring_snapshot_saved_at", v0=st.get("saved_at", "unknown")))
    n, fails = run_restore(st, emit)
    if fails and not force_off:
        if "session" in st and not is_dry_run():
            st["session"]["restored"] += n
            st["session"]["restore_failures"] += fails
            state_api.save_state(st)
        emit("bad", t("impl.restores_failed_snapshot_retained_resolve_the", v0=fails))
        runtime._finish_dry_run()
        return 1
    from .. import sessions

    report = sessions.finish(st, n, fails)
    if report is not None:
        for line in sessions.describe(report, sessions.read_history(), verbose=verbose):
            c.print(line, markup=False, highlight=False)
    state_target = state_path()
    if is_dry_run():
        from ..system.shell import dry_run_state_dir

        state_target = dry_run_state_dir() / "state.json"
    state_target.unlink(missing_ok=True)
    if fails:
        emit("bad", t("impl.restores_failed_snapshot_discarded_by_force", v0=fails))
    else:
        emit("ok", t("impl.state_file_cleared_items_restored", v0=n))
    runtime._finish_dry_run()
    return 0

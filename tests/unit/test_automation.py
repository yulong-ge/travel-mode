"""Optional event automation is hermetic, bounded and non-interactive."""

import json
import os
import plistlib
from pathlib import Path

import pytest
from jsonschema import validate

from travel_mode import automation, cli, state
from travel_mode.actions.automation import InstallAutomation, UninstallAutomation
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.config import ConfigError, load_config
from travel_mode.flows import automation as flow
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


@pytest.fixture
def machine(tmp_paths, fake_runner, monkeypatch, darwin_platform):
    monkeypatch.setattr(automation, "executable", lambda: "/fixture/bin/travel-mode")
    fake_runner.responses[("launchctl", "print")] = CmdResult("not found", "", 113)
    fake_runner.responses[("launchctl", "bootstrap")] = CmdResult("", "", 0)
    fake_runner.responses[("launchctl", "bootout")] = CmdResult("", "", 0)
    fake_runner.responses[("osascript",)] = CmdResult("", "", 0)
    return fake_runner


def configure(tmp_paths, battery="notify", ac="notify", retain=50):
    (tmp_paths / "config.toml").write_text(
        f'[automation]\non_battery="{battery}"\non_ac="{ac}"\nretain={retain}\n'
    )


def test_plist_has_only_event_launch_and_absolute_program(machine):
    data = automation.agent_plist()
    assert set(data) == automation.PLIST_KEYS
    assert not set(data) & {
        "KeepAlive",
        "RunAtLoad",
        "StartInterval",
        "StartCalendarInterval",
        "WatchPaths",
        "QueueDirectories",
    }
    assert data["LaunchEvents"] == {
        "com.apple.notifyd.matching": {
            "power-changed": {"Notification": "com.apple.system.powersources.source"}
        }
    }
    assert data["ThrottleInterval"] == 30
    assert data["ProgramArguments"] == [
        "/fixture/bin/travel-mode",
        "event",
        "power-changed",
        "--launch-event",
    ]


def test_install_status_and_uninstall_use_exact_executor_commands(machine, capsys):
    assert cli.main(["automation", "install"]) == 0
    assert plistlib.loads(automation.agent_path().read_bytes()) == automation.agent_plist()
    target = f"gui/{os.getuid()}/{automation.LABEL}"
    machine.responses[("launchctl", "print")] = CmdResult("state = not running\n", "", 0)
    assert cli.main(["automation", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["installed"] and result["loaded"] and not result["running"]
    validate(result, json.loads(Path("docs/json-schema/automation-status.schema.json").read_text()))
    assert cli.main(["automation", "uninstall"]) == 0
    assert not automation.agent_path().exists()
    assert ("launchctl", "bootstrap", f"gui/{os.getuid()}", str(automation.agent_path())) in machine.calls
    assert ("launchctl", "bootout", target) in machine.calls
    assert machine.calls == [
        ("launchctl", "print", target),
        ("launchctl", "bootstrap", f"gui/{os.getuid()}", str(automation.agent_path())),
        ("launchctl", "print", target),
        ("launchctl", "print", target),
        ("launchctl", "bootout", target),
    ]
    assert not any(call[0] == "sudo" for call in machine.calls)


def test_install_dry_run_has_no_files_or_backend_mutations(machine, capsys):
    path = automation.agent_path()
    assert cli.main(["automation", "install", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "LaunchEvents" in output and "bootstrap" in output
    assert not path.exists()
    assert machine.calls and all(call[:2] == ("launchctl", "print") for call in machine.calls)


def test_uninstall_dry_run_preserves_owned_plist(machine):
    Executor(Context(None)).execute(InstallAutomation())
    path = automation.agent_path()
    payload = path.read_bytes()
    machine.responses[("launchctl", "print")] = CmdResult("state = not running", "", 0)
    shell.enable_dry_run()
    try:
        Executor(Context(None)).execute(UninstallAutomation())
        assert path.read_bytes() == payload
        assert ("launchctl", "bootout", f"gui/{os.getuid()}/{automation.LABEL}") not in machine.calls
    finally:
        shell.disable_dry_run()


def test_cli_dry_run_uninstall_previews_original_target_without_unlink(machine, capsys):
    Executor(Context(None)).execute(InstallAutomation())
    path, payload = automation.agent_path(), automation.agent_path().read_bytes()
    machine.responses[("launchctl", "print")] = CmdResult("state = not running", "", 0)
    machine.calls.clear()
    assert cli.main(["automation", "uninstall", "--dry-run"]) == 0
    assert str(path) in capsys.readouterr().out
    assert path.read_bytes() == payload
    assert all(call[:2] == ("launchctl", "print") for call in machine.calls)


def test_foreign_plist_is_not_modified(machine):
    path = automation.agent_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(plistlib.dumps({"Label": "other", "KeepAlive": True}))
    with pytest.raises(automation.AutomationError):
        Executor(Context(None)).execute(UninstallAutomation())


def test_missing_executable_is_explicit(monkeypatch):
    monkeypatch.setattr("travel_mode.automation.sysconfig.get_path", lambda _: "/missing/fixture")
    with pytest.raises(automation.AutomationError):
        automation.executable()


@pytest.mark.parametrize("battery", ["notify", "apply-work-safe", "off"])
@pytest.mark.parametrize("ac", ["notify", "off-restore", "off"])
@pytest.mark.parametrize("source", ["battery", "ac"])
def test_policy_matrix_only_explicit_mutation_policies_act(
    machine, tmp_paths, monkeypatch, battery, ac, source
):
    configure(tmp_paths, battery, ac)
    machine.responses[("pmset", "-g", "ps")] = CmdResult(
        "Battery Power" if source == "battery" else "AC Power", "", 0
    )
    monkeypatch.setattr(state, "load_state", lambda: {"fixture": True})
    applied, restored = [], []
    monkeypatch.setattr(flow, "apply_work_safe", lambda config, details: applied.append(True))
    monkeypatch.setattr(
        "travel_mode.actions.automation.RestoreAutomation.apply",
        lambda self, ctx: restored.append(True) or True,
    )
    assert flow.event(now=lambda: 100) == 0
    assert bool(applied) == (source == "battery" and battery == "apply-work-safe")
    assert bool(restored) == (source == "ac" and ac == "off-restore")
    assert any(call[0] == "osascript" for call in machine.calls) == (
        (battery if source == "battery" else ac) != "off"
    )


def test_default_notification_never_executes_actions_or_authenticates(machine):
    assert flow.event(now=lambda: 100) == 0
    assert all(call[0] in {"pmset", "osascript"} for call in machine.calls)
    assert not state._path().exists()


def test_duplicate_events_do_not_repeat_actions_but_opposite_transition_is_not_debounced(
    machine, monkeypatch
):
    monkeypatch.setattr(state, "load_state", lambda: {})
    assert flow.event(now=lambda: 100) == 0
    assert flow.event(now=lambda: 101) == 0
    assert flow.event(now=lambda: 131) == 0
    assert sum(c[0] == "osascript" for c in machine.calls) == 1
    machine.responses[("pmset", "-g", "ps")] = CmdResult("AC Power", "", 0)
    assert flow.event(now=lambda: 132) == 0
    assert sum(c[0] == "osascript" for c in machine.calls) == 2
    assert [row["result"] for row in automation.read_log()["entries"]] == [
        "handled",
        "ignored",
        "ignored",
        "handled",
    ]


def test_notification_failure_is_logged_after_actions_and_returns_nonzero(machine, tmp_paths, monkeypatch):
    configure(tmp_paths, "apply-work-safe")
    actions = []
    monkeypatch.setattr(flow, "apply_work_safe", lambda config, details: actions.append(True))
    machine.responses[("osascript",)] = CmdResult("", "notification unavailable", 1)
    assert flow.event(now=lambda: 100) == 1
    assert actions == [True]
    row = automation.read_log()["entries"][-1]
    assert row["result"] == "failed" and "notification unavailable" in row["details"][-1]


def test_event_failure_preserves_cause_and_bounded_log(machine, tmp_paths):
    configure(tmp_paths, retain=2)
    machine.responses[("pmset", "-g", "ps")] = CmdResult("", "read error", 1)
    for when in range(4):
        with pytest.raises(shell.ReadError, match="read error"):
            flow.event(now=lambda when=when: when)
    rows = automation.read_log()["entries"]
    assert len(rows) == 2 and [row["at"] for row in rows] == [2, 3]
    assert all(row["result"] == "failed" for row in rows)


def test_native_event_consumed_before_config_error_is_logged(machine, tmp_paths, monkeypatch):
    consumed = []
    monkeypatch.setattr("travel_mode.system.events.consume_once", lambda: consumed.append(True))
    (tmp_paths / "config.toml").write_text('[automation]\non_battery="invalid"\n')
    with pytest.raises(ConfigError):
        flow.event(launch_event=True, now=lambda: 100)
    assert consumed == [True] and automation.read_log()["entries"][-1]["result"] == "failed"


def test_event_sudo_is_noninteractive_even_with_a_terminal(machine, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    machine.responses[("sudo", "-n", "pmset")] = CmdResult("", "password required", 1)
    with flow.event_commands():
        output, rc = shell.run_rc(["pmset", "-b", "lowpowermode", "1"], sudo=True)
    assert rc == 1 and output == "password required"
    assert machine.calls == [("sudo", "-n", "pmset", "-b", "lowpowermode", "1")]


def test_event_deadline_and_interactive_attempt_fail_explicitly(machine):
    ticks = iter([0, 46])
    with flow.event_commands(clock=lambda: next(ticks)), pytest.raises(automation.AutomationError):
        shell.run_rc(["pmset", "-g", "ps"])
    assert machine.calls == []
    with flow.event_commands(), pytest.raises(automation.AutomationError):
        shell.run_rc(["sudo", "-v"], interactive=True)


@pytest.mark.parametrize(
    "key,value",
    [("on_battery", '"invalid"'), ("on_ac", '"apply-work-safe"'), ("retain", "false"), ("retain", "0")],
)
def test_invalid_automation_config_is_explicit(tmp_paths, key, value):
    (tmp_paths / "config.toml").write_text(f"[automation]\n{key}={value}\n")
    with pytest.raises(ConfigError, match="automation"):
        load_config()


def test_safe_work_only_uses_safe_actions_and_explicit_apps(machine, tmp_paths, monkeypatch):
    from travel_mode.actions.apps import FreezeApp, QuitApp, UnloadAgent
    from travel_mode.actions.power import SetPowerProfile

    (tmp_paths / "config.toml").write_text(
        '[automation]\non_battery="apply-work-safe"\n[apps]\nfreeze=["Worker"]\nquit=["Explicit"]\n'
    )
    monkeypatch.setattr(
        flow.launchd,
        "svc_scan",
        lambda: {
            "unloadable": [
                {"plist_path": "/fixture/agent.plist"},
                {"plist_path": "/fixture/events.plist", "label": automation.LABEL},
            ]
        },
    )
    monkeypatch.setattr(
        flow.apps,
        "scan_apps",
        lambda: {
            "running": [
                {"name": "Worker", "path": "/fixture/Worker.app", "defact": "freeze"},
                {"name": "Explicit", "path": "/fixture/Explicit.app", "defact": "quit"},
                {"name": "OneDrive", "path": "/fixture/OneDrive.app", "defact": "quit"},
            ]
        },
    )
    actions = []

    def execute(self, action):
        actions.append(action)
        if type(action).__name__ == "Notify":
            return "", 0
        return ("", 0) if isinstance(action, (SetPowerProfile, UnloadAgent)) else True

    monkeypatch.setattr(Executor, "execute", execute)
    assert flow.event(now=lambda: 100) == 0
    assert [type(action) for action in actions[:-1]] == [SetPowerProfile, UnloadAgent, FreezeApp, QuitApp]
    assert all(action.risk == "safe" for action in actions)
    assert not any(getattr(action, "path", "").endswith("OneDrive.app") for action in actions)


def test_password_required_skips_power_but_continues_other_safe_actions(machine, tmp_paths):
    configure(tmp_paths, "apply-work-safe")
    machine.responses[("sudo", "-n", "-l")] = CmdResult("", "password required", 1)
    assert flow.event(now=lambda: 100) == 0
    assert not any(call[:3] == ("sudo", "-n", "pmset") for call in machine.calls)
    assert not state._path().exists()
    row = automation.read_log()["entries"][-1]
    assert any("password required" in detail for detail in row["details"])
    assert any(call[0] == "osascript" and "password required" in call[-1] for call in machine.calls)


def test_real_safe_work_path_keeps_recovery_receipt_and_avoids_measurement(machine, tmp_paths):
    configure(tmp_paths, "apply-work-safe")
    machine.responses[("sudo", "-n", "pmset")] = CmdResult("", "", 0)
    assert flow.event(now=lambda: 100) == 0
    snapshot = state.load_state()
    assert snapshot["scenario"] == "work" and snapshot["batt_prof"]
    assert snapshot["session"]["actions"]["succeeded"] == 1
    assert "measurement" not in snapshot
    assert ("sudo", "-n", "pmset", "-b", "lowpowermode", "1") in machine.calls
    assert not any(call[:2] == ("sudo", "-v") for call in machine.calls)


def test_launch_event_cli_finishes_scopes_before_native_process_exit(machine, monkeypatch):
    consumed, exits = [], []
    monkeypatch.setattr("travel_mode.system.events.consume_once", lambda: consumed.append(True))
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))
    original = shell.get_runner()
    cli.main(["event", "power-changed", "--launch-event"])
    assert consumed == [True] and exits == [0]
    assert automation.read_log()["entries"][-1]["result"] == "handled"
    assert shell.get_runner() is original


def test_automatic_restore_never_authenticates_and_retains_failed_snapshot(machine, tmp_paths):
    configure(tmp_paths, ac="off-restore")
    snapshot = state.new_state()
    snapshot["batt_prof"] = "lowpowermode 0"
    state.save_state(snapshot)
    machine.responses[("pmset", "-g", "ps")] = CmdResult("AC Power", "", 0)
    machine.responses[("sudo", "-n", "pmset")] = CmdResult("", "password required", 1)
    with pytest.raises(automation.AutomationError):
        flow.event(now=lambda: 100)
    assert state.load_state()["batt_prof"] == "lowpowermode 0"
    assert all(call[1] == "-n" for call in machine.calls if call[0] == "sudo")
    assert automation.read_log()["entries"][-1]["result"] == "failed"


def test_automatic_restore_uses_existing_receipts_and_clears_snapshot(machine, tmp_paths):
    configure(tmp_paths, ac="off-restore")
    snapshot = state.new_state()
    snapshot["batt_prof"] = "lowpowermode 0"
    state.save_state(snapshot)
    machine.responses[("pmset", "-g", "ps")] = CmdResult("AC Power", "", 0)
    machine.responses[("sudo", "-n", "pmset")] = CmdResult("", "", 0)
    assert flow.event(now=lambda: 100) == 0
    assert state.load_state() is None
    assert ("sudo", "-n", "pmset", "-b", "lowpowermode", "0") in machine.calls


def test_status_reports_observation_failure_instead_of_claiming_unloaded(machine):
    machine.responses[("launchctl", "print")] = CmdResult("", "permission denied", 1)
    with pytest.raises(automation.AutomationError, match="permission denied"):
        automation.status()


@pytest.mark.parametrize("language,yes,no", [("en", "yes", "no"), ("zh", "是", "否")])
def test_plain_status_localizes_booleans_and_preserves_json(machine, monkeypatch, capsys, language, yes, no):
    monkeypatch.setenv("TRAVEL_MODE_LANG", language)
    assert cli.main(["automation", "install"]) == 0
    capsys.readouterr()
    machine.responses[("launchctl", "print")] = CmdResult("state = not running\n", "", 0)
    assert cli.main(["automation", "status"]) == 0
    text = capsys.readouterr().out
    assert yes in text and no in text
    assert not any(word in text for word in ("True", "False", "None"))
    assert cli.main(["automation", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["installed"] is True and result["loaded"] is True and result["running"] is False
    assert result["last_event"] is None


def test_invalid_plist_reports_its_file(machine):
    path = automation.agent_path()
    path.parent.mkdir(parents=True)
    path.write_text("<plist>broken")
    with pytest.raises(automation.AutomationError, match=str(path)):
        automation.read_agent()


@pytest.mark.parametrize(
    "value",
    [
        "{}",
        '{"version":true,"entries":[]}',
        '{"version":1,"entries":[{"at":0,"source":"ac","result":[],"details":[]}]}',
    ],
)
def test_invalid_event_log_is_an_explicit_state_error(machine, value):
    path = automation.log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    with pytest.raises(state.StateError, match=r"automation\.json"):
        automation.read_log()

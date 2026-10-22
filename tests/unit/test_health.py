"""Health observations use generic fixtures, finite clocks and strict command doubles."""

import json
import os
from dataclasses import replace

import pytest

from travel_mode.actions.apps import FreezeApp, QuitApp, QuitDaemonApp
from travel_mode.actions.power import (
    EnableSleep,
    KillCaffeinate,
    SetBrightness,
    SetPowerProfile,
    StopSpotlight,
)
from travel_mode.config import AppsConfig, Config, ConfigError, HealthConfig, load_config
from travel_mode.flows.doctor import make_action, remediate
from travel_mode.system import health
from travel_mode.system.shell import CmdResult, _dry_run_readonly, set_runner


def process(pid, executable, cpu=0, *, uid=None, ppid=1, stat="S"):
    owner = os.getuid() if uid is None else uid
    return f"{pid} {ppid} {owner} {stat} {cpu} Thu Oct 15 12:00:00 2026 {executable}"


def response(fake, command, output, rc=0):
    fake.responses[tuple(command)] = CmdResult(output, "", rc)


@pytest.fixture
def health_machine(fake_runner):
    response(fake_runner, health.PROCESS_COMMAND, process(900000, "/System/Library/CoreServices/loginwindow"))
    response(fake_runner, ["pmset", "-g"], "SleepDisabled 0\n lowpowermode 1\n")
    return fake_runner


def rows(config=None):
    return {r["id"]: r for r in health.collect(config, clock=lambda: 0)["checks"]}


def test_all_clear_and_bounded_cached_reads(health_machine):
    report = health.collect(clock=lambda: 0)
    assert len(report["checks"]) == 9
    assert {r["status"] for r in report["checks"]} == {"clear"}
    assert report["budget_seconds"] == 15
    assert health_machine.calls.count(tuple(health.PROCESS_COMMAND)) == 1
    assert health_machine.calls.count(("pmset", "-g", "ps")) == 1
    assert not any(c[0] in {"top", "sudo"} for c in health_machine.calls)


HITS = {
    "sleep_disabled": (["pmset", "-g"], "SleepDisabled 1\nlowpowermode 1\n", "sleep"),
    "keep_awake": (
        health.PROCESS_COMMAND,
        process(900000, "/Applications/Amphetamine.app/Contents/MacOS/Amphetamine"),
        "quit",
    ),
    "assertions": (
        ["pmset", "-g", "assertions"],
        'Assertion status system-wide:\npid 900000(Worker): PreventSystemSleep named: "busy"',
        "quit",
    ),
    "displays": (health.native_command("displays"), '[{"id": 99, "builtin": false}]', None),
    "spotlight": (health.PROCESS_COMMAND, process(901, "/System/Library/mds_stores", 30), "spotlight"),
    "backup": (["tmutil", "status"], "Backup session status:\n{ Running = 1; }", None),
    "low_power": (["pmset", "-g"], "SleepDisabled 0\nlowpowermode 0\n", "low_power"),
    "brightness": (health.native_command("brightness"), "0.9", "brightness"),
    "high_cpu": (
        health.PROCESS_COMMAND,
        process(900000, "/System/Library/CoreServices/loginwindow")
        + "\n"
        + process(902, "/Applications/Worker.app/Contents/MacOS/Worker", 75),
        "freeze",
    ),
}


@pytest.mark.parametrize("key", list(HITS))
def test_each_check_hit_and_actions(key, health_machine):
    command, output, action = HITS[key]
    if key == "assertions":
        response(
            health_machine,
            health.PROCESS_COMMAND,
            process(900000, "/Applications/Worker.app/Contents/MacOS/Worker"),
        )
    response(health_machine, command, output)
    result = rows()[key]
    assert result["status"] == "hit", result
    assert result["evidence"] and result["impact"] and result["manual"]
    assert [a["kind"] for a in result["actions"]] == ([action] if action else [])


@pytest.mark.parametrize("key", list(HITS))
@pytest.mark.parametrize("output,rc", [("permission denied", 1), ("timeout", 124), ("broken format", 0)])
def test_each_check_read_or_parse_failure_is_explicit(key, output, rc, health_machine):
    command = HITS[key][0]
    response(health_machine, command, output, rc)
    result = rows()[key]
    assert result["status"] == "unknown", result
    assert result["error"] and result["actions"] == []
    # Independent Time Machine/display checks survive an unrelated source failure.
    if key not in {"backup", "displays"}:
        assert rows()["backup"]["status"] == "clear"


def test_caffeinate_full_command_parent_and_exact_action(health_machine):
    response(
        health_machine,
        health.PROCESS_COMMAND,
        "\n".join(
            [
                process(901, "/usr/bin/caffeinate", ppid=902),
                process(902, "/Applications/Terminal.app/Contents/MacOS/Terminal"),
            ]
        ),
    )
    response(health_machine, ["ps", "-ww", "-p", "901", "-o", "command="], "/usr/bin/caffeinate -i -t 300")
    response(
        health_machine,
        ["pmset", "-g", "assertions"],
        'Assertion status system-wide:\npid 901(caffeinate): PreventUserIdleSystemSleep named: "caffeinate"',
    )
    row = rows()["keep_awake"]
    assert "-i -t 300" in row["evidence"][0] and "PPID 902" in row["evidence"][0]
    action = make_action(row["actions"][0], {}, None)
    assert isinstance(action, KillCaffeinate)
    assert action.target.process.pid == 901
    assert action.target.process.started == "Thu Oct 15 12:00:00 2026"
    response(health_machine, ["pmset", "-g", "assertions"], "Assertion status system-wide:\n")
    assert rows()["keep_awake"]["status"] == "hit"
    assert rows()["keep_awake"]["actions"] == []


def test_assertions_exclude_system_owners_and_preserve_unknown_owner(health_machine):
    response(
        health_machine,
        health.PROCESS_COMMAND,
        "\n".join(
            [
                process(900000, "/System/Library/CoreServices/loginwindow"),
                process(901, "/System/Library/PrivateFrameworks/powerd"),
                process(902, "/Applications/Worker.app/Contents/MacOS/Worker"),
                process(903, "/Applications/Other.app/Contents/MacOS/Other", uid=os.getuid() + 1),
            ]
        ),
    )
    lines = ["Assertion status system-wide:"] + [
        f'pid {pid}({name}): PreventSystemSleep named: "busy"'
        for pid, name in [(901, "powerd"), (902, "Worker"), (903, "Other"), (904, "Vanished")]
    ]
    response(health_machine, ["pmset", "-g", "assertions"], "\n".join(lines))
    row = rows()["assertions"]
    assert len(row["evidence"]) == 3
    assert all(str(pid) in line for pid, line in zip((902, 903, 904), row["evidence"], strict=True))
    assert len(row["actions"]) == 1


def test_spotlight_enabled_is_not_active_and_disabled_never_suggests_stop(health_machine):
    assert rows()["spotlight"]["status"] == "clear"
    response(health_machine, health.PROCESS_COMMAND, process(901, "/System/Library/mdworker_shared", 85))
    response(health_machine, ["mdutil", "-s", "/"], "/: Indexing disabled.")
    assert rows()["spotlight"]["status"] == "clear"
    assert rows()["spotlight"]["actions"] == []


def test_cpu_foreground_helpers_system_terminal_ancestors_and_other_users_excluded(health_machine):
    entries = [
        process(900000, "/Applications/Editor.app/Contents/MacOS/Editor", 90),
        process(901, "/Applications/Editor.app/Contents/Helpers/Worker", 95),
        process(902, "/System/Library/Worker", 99),
        process(903, "/usr/libexec/Worker", 99),
        process(904, "/Applications/Terminal.app/Contents/MacOS/Terminal", 99),
        process(905, "/Applications/Other.app/Contents/MacOS/Other", 99, uid=os.getuid() + 1),
        process(906, "/Applications/Stopped.app/Contents/MacOS/Stopped", 99, stat="T"),
        process(os.getpid(), "/Applications/Tool.app/Contents/MacOS/Tool", 99, ppid=907),
        process(907, "/Applications/Parent.app/Contents/MacOS/Parent", 99),
        process(908, "/Applications/Docker.app/Contents/MacOS/Docker", 50),
        process(909, "/opt/demo/bin/task", 40),
    ]
    response(health_machine, health.PROCESS_COMMAND, "\n".join(entries))
    row = rows()["high_cpu"]
    assert len(row["evidence"]) == 2
    assert "vm" in row["evidence"][0] and "909" in row["evidence"][1]
    assert row["actions"] == [dict(kind="quit", path="/Applications/Docker.app", name="Docker")]


def test_ac_skips_native_brightness_and_low_power(health_machine):
    response(health_machine, ["pmset", "-g", "ps"], "Now drawing from 'AC Power'")
    result = rows()
    assert result["brightness"]["status"] == result["low_power"]["status"] == "clear"
    assert tuple(health.native_command("brightness")) not in health_machine.calls


def test_keep_skip_ignore_override_health_actions(health_machine):
    response(
        health_machine,
        health.PROCESS_COMMAND,
        process(900000, "/Applications/Amphetamine.app/Contents/MacOS/Amphetamine"),
    )
    config = replace(Config.defaults(), apps=AppsConfig(skip=("Amphetamine",)))
    assert rows(config)["keep_awake"]["status"] == "hit"
    assert rows(config)["keep_awake"]["actions"] == []


def test_brightness_target_and_threshold(health_machine):
    response(health_machine, health.native_command("brightness"), "0.8")
    config = replace(Config.defaults(), health=HealthConfig(brightness_threshold=0.9))
    assert rows(config)["brightness"]["status"] == "clear"
    config = replace(config, health=HealthConfig(brightness_target=0.4))
    assert rows(config)["brightness"]["actions"] == [dict(kind="brightness", value=0.4)]


def test_budget_exhaustion_is_explicit_and_no_more_commands(health_machine):
    now = [0.0]
    timeouts = []

    def runner(cmd, timeout=None, interactive=False):
        timeouts.append(timeout)
        now[0] += timeout
        return CmdResult("", "timeout", 124)

    set_runner(runner)
    report = health.collect(clock=lambda: now[0])
    assert all(r["status"] == "unknown" for r in report["checks"])
    assert report["elapsed_seconds"] <= 15
    assert all(0 < timeout <= 2 for timeout in timeouts)


@pytest.mark.parametrize(
    "descriptor,expected",
    [
        ({"kind": "sleep"}, EnableSleep),
        ({"kind": "spotlight"}, StopSpotlight),
        ({"kind": "low_power", "key": "powermode"}, SetPowerProfile),
        ({"kind": "brightness", "value": 0.5}, SetBrightness),
        ({"kind": "quit", "path": "/Applications/Worker.app", "name": "Worker"}, QuitApp),
        ({"kind": "freeze", "path": "/Applications/Worker.app"}, FreezeApp),
    ],
)
def test_remediation_reuses_actions_and_returns_failure(descriptor, expected):
    calls, saves = [], []

    def execute(action):
        calls.append(action)
        return ("", 0) if expected in (EnableSleep, StopSpotlight, SetPowerProfile) else True

    remediate(descriptor, execute, {}, lambda: saves.append(True))
    assert isinstance(calls[0], expected) and saves == [True]
    assert remediate(descriptor, lambda _: False, {}, lambda: saves.append(True)) is False
    assert saves == [True]


def test_configured_daemon_uses_existing_daemon_action(tmp_paths):
    (tmp_paths / "config.toml").write_text(
        '[[apps.with_daemon]]\nname="Worker"\nsystem_daemons=["/Library/LaunchDaemons/org.demo.worker.plist"]\n'
    )
    assert isinstance(
        make_action(dict(kind="quit", name="Worker", path="/Applications/Worker.app"), {}, None),
        QuitDaemonApp,
    )


@pytest.mark.parametrize("value", ["true", "0", "-1", "nan", "1.1"])
def test_health_config_validates_numbers(tmp_paths, value):
    path = tmp_paths / "config.toml"
    path.write_text(f"[health]\nbrightness_threshold={value}\n")
    with pytest.raises(ConfigError, match=r"health\.brightness_threshold"):
        load_config(path)


def test_native_command_readonly_allowlist():
    assert _dry_run_readonly(health.native_command("displays"))
    assert _dry_run_readonly(health.native_command("brightness"))
    assert not _dry_run_readonly(health.native_command("set-brightness"))
    assert not _dry_run_readonly(["tmutil", "stopbackup"])
    assert json.dumps(health.collect(clock=lambda: 0))


def test_expired_global_budget_marks_remaining_checks_without_commands(health_machine):
    ticks = iter([0, 16] + [16] * 40)
    report = health.collect(clock=lambda: next(ticks))
    assert all(r["status"] == "unknown" for r in report["checks"])
    assert all(r["error"] for r in report["checks"])
    assert health_machine.calls == []


def test_foreground_disappearance_does_not_misidentify_app_as_background(health_machine):
    response(
        health_machine,
        health.PROCESS_COMMAND,
        process(901, "/Applications/Worker.app/Contents/MacOS/Worker", 80),
    )
    assert rows()["high_cpu"]["status"] == "unknown"


def test_unexpected_programming_error_is_not_converted_to_clear(health_machine, monkeypatch):
    def broken(session):
        raise TypeError("programming error")

    # Keep valid message keys so the failure is inside the probe.
    monkeypatch.setattr(health, "CHECKS", [("sleep_disabled", broken)])
    with pytest.raises(TypeError, match="programming error"):
        health.collect()


def test_macos_signed_nobody_uid_is_valid_and_excluded_from_background(health_machine):
    response(
        health_machine,
        health.PROCESS_COMMAND,
        "\n".join(
            [
                process(900000, "/System/Library/CoreServices/loginwindow"),
                process(903, "/usr/libexec/demo", 50, uid=-2),
            ]
        ),
    )
    result = rows()
    for key in ("keep_awake", "assertions", "spotlight", "high_cpu"):
        assert result[key]["status"] == "clear", result[key]


def test_empty_process_snapshot_and_malformed_assertion_fail_explicitly(health_machine):
    with pytest.raises(health.ObservationError, match="empty process snapshot"):
        health.parse_processes("")
    response(
        health_machine,
        ["pmset", "-g", "assertions"],
        "Assertion status system-wide:\npid broken PreventSystemSleep",
    )
    assert rows()["assertions"]["status"] == "unknown"


def test_identification_only_product_never_becomes_health_action(health_machine):
    response(
        health_machine,
        health.PROCESS_COMMAND,
        "\n".join(
            [
                process(900000, "/System/Library/CoreServices/loginwindow"),
                process(901, "/Applications/Backblaze.app/Contents/MacOS/Backblaze", 50),
            ]
        ),
    )
    # The public Backblaze rule is notice-only; no health path may turn it into a freeze.
    row = rows()["high_cpu"]
    assert row["status"] == "hit" and row["actions"] == []


@pytest.mark.parametrize("command", [["pmset", "-g"], ["pmset", "-g", "ps"], ["mdutil", "-s", "/"]])
@pytest.mark.parametrize("as_json", [False, True])
def test_cli_shared_read_failure_preserves_all_independent_checks(
    command, as_json, health_machine, darwin_platform, capsys
):
    from pathlib import Path

    from jsonschema import validate

    from travel_mode.cli import main

    response(health_machine, command, "read denied", 1)
    assert main(["check"] + (["--json"] if as_json else [])) == 0
    output = capsys.readouterr().out
    assert "Traceback" not in output
    if as_json:
        data = json.loads(output)
        validate(data, json.loads(Path("docs/json-schema/check.schema.json").read_text()))
        assert data["audit_error"]
        assert len(data["health"]["checks"]) == 9
        assert next(r for r in data["health"]["checks"] if r["id"] == "backup")["status"] == "clear"
    else:
        from travel_mode.i18n import t

        assert t("health.backup.title") in output and t("health.unknown") in output

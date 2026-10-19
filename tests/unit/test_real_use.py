"""Regressions from real sessions, with finite clocks and sealed observations."""

from datetime import datetime, timezone

import pytest

from travel_mode import health_view, state, status
from travel_mode.system import apps, launchd, pmset
from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize("rc,detail", [(1, "User canceled (-128)"), (124, "timeout")])
def test_delayed_main_exit_after_event_failure_is_success(rc, detail, fake_runner, monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(apps.time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    monkeypatch.setattr(apps, "app_running", lambda path: elapsed[0] < 3)
    monkeypatch.setattr(apps, "bundle_alive", lambda path: elapsed[0] < 3)
    monkeypatch.setattr(apps, "app_bid", lambda path: "org.demo.delayed")
    fake_runner.responses[("osascript",)] = CmdResult("", detail, rc)
    result = apps.grace_quit("/fixture/Delayed.app", "Delayed")
    assert result and elapsed[0] == 3


def test_refusal_waits_full_window_before_failure(fake_runner, monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(apps.time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    monkeypatch.setattr(apps, "app_running", lambda path: True)
    monkeypatch.setattr(apps, "app_bid", lambda path: "org.demo.refusing")
    fake_runner.responses[("osascript",)] = CmdResult("", "User canceled (-128)", 1)
    result = apps.grace_quit("/fixture/Refusing.app", "Refusing")
    assert not result and result.status == "refused" and elapsed[0] == 10


def test_main_exit_with_lingering_helpers_is_success_and_explained(fake_runner, monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(apps.time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    monkeypatch.setattr(apps, "app_running", lambda path: elapsed[0] < 3)
    monkeypatch.setattr(apps, "bundle_alive", lambda path: True)
    monkeypatch.setattr(apps, "app_bid", lambda path: "org.demo.helpers")
    fake_runner.responses[("osascript",)] = CmdResult("", "User canceled (-128)", 1)
    result = apps.grace_quit("/fixture/Helpers.app", "Helpers")
    assert result and result.helpers_remaining and elapsed[0] == 10
    assert "辅助进程" in result.explanation("Helpers")


def test_assertions_have_sleep_scope_and_duration():
    raw = 'pid 42(Remote): [0x0001] 121:00:00 PreventUserIdleSystemSleep named: "remote"'
    row = pmset.sleep_blocking_assertions([raw])[0]
    assert row["scope"] == "idle" and row["held_seconds"] == 121 * 3600
    raw = raw.replace("PreventUserIdleSystemSleep", "PreventSystemSleep")
    assert pmset.sleep_blocking_assertions([raw])[0]["scope"] == "system"


def test_health_default_view_is_human_readable_but_preserves_evidence():
    row = dict(
        id="low_power",
        title="低电量模式",
        status="clear",
        evidence=["lowpowermode=1"],
        impact="",
        manual="",
        actions=[],
        error=None,
    )
    view = health_view.presentation(dict(checks=[row]), scenario="work")[0]
    assert view["summary"] == "✓ 低电量模式：已开启"
    assert view["state"] == "normal" and "lowpowermode" not in view["summary"]
    assert row["evidence"] == ["lowpowermode=1"]


def test_idle_assertion_is_advisory_for_away_and_hides_technical_details():
    row = dict(
        id="assertions",
        title="assertions",
        status="hit",
        error=None,
        actions=[],
        impact="",
        manual="",
        evidence=['pid 42(Remote): [0x0001] 121:00:00 PreventUserIdleSystemSleep named: "remote"'],
    )
    view = health_view.presentation(dict(checks=[row]), scenario="away")[0]
    assert view["advisory"] and view["state"] == "normal"
    assert "合盖后仍会睡眠" in view["summary"] and "5 天" in view["summary"]
    assert "PID" not in view["summary"] and "0x0001" not in view["summary"]


def test_status_does_not_use_pmset_remaining_without_session(fake_runner):
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult(
        "Now drawing from 'Battery Power'\n -InternalBattery-0 62%; discharging; 5:30 remaining", "", 0
    )
    data = status.data()
    assert data["battery"]["remaining_minutes"] is None
    assert "预计剩余" not in status.power_line(data)


def test_status_uses_one_session_accumulator_interval(fake_runner, monkeypatch):
    snapshot = state.new_state()
    snapshot["scenario"] = "work"
    from travel_mode import sessions

    sessions.begin(snapshot)
    snapshot["session"]["started_at"] = "2026-10-19T10:00:00+00:00"
    snapshot["session"]["start"]["telemetry"] = dict(system_count=100, system_sum_mw=1_000_000)
    snapshot["session"]["external_seen"] = False
    monkeypatch.setattr(status, "load_state", lambda: snapshot)
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult(
        "Battery Power\n -InternalBattery-0 62%; discharging; 1:30 remaining", "", 0
    )
    from travel_mode.system import battery

    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return dict(
            available=True,
            external_connected=False,
            is_charging=False,
            telemetry=dict(system_count=110, system_sum_mw=1_098_000),
            power_source="PowerTelemetryData.SystemLoad",
            remaining_wh=35.9333333,
        )

    monkeypatch.setattr(battery, "read", read)
    result = status.data(now=datetime(2026, 10, 19, 11, tzinfo=timezone.utc))
    assert calls == [dict(timeout=2)]
    assert result["battery"]["average_watts"] == pytest.approx(9.8)
    assert result["battery"]["remaining_minutes"] == 220
    assert "按本次平均 9.8 W" in status.power_line(result)


def test_power_preview_separates_unchanged_from_changes(fake_runner):
    view = pmset.power_preview(
        {"b": {"lowpowermode": "1", "powernap": "0"}, "a": {"powernap": "1", "womp": "0"}}, scenario="work"
    )
    assert not view["changes"]
    assert "低电量模式" in view["satisfied"]
    assert "1 → 1" not in str(view)
    assert "接电时关闭 Power Nap" in view["ac_changes"][0]["explanation"]


@pytest.mark.parametrize("terminal", ["exited", "frozen", "skipped"])
def test_app_row_terminal_states_have_no_retry_or_force(terminal):
    from travel_mode.tui.view_models import app_row

    row = app_row(
        dict(name="Demo", path="/fixture/Demo.app", defact="quit"), state=terminal, failure="refused"
    )
    assert not {"retry_quit", "force_quit"} & set(row["operations"]) and row["state"] == terminal


def test_launchctl_discovery_error_is_typed_and_logged(fake_runner):
    from travel_mode.failures import CommandError, log_path

    fake_runner.responses[("launchctl", "list")] = CmdResult("", "fixture permission denied", 1)
    with pytest.raises(CommandError):
        launchd.svc_scan()
    assert "fixture permission denied" in log_path().read_text()


def test_invalid_agent_plist_gets_failure_log(tmp_path, fake_runner):
    from travel_mode.failures import log_path

    (tmp_path / "org.demo.bad.plist").write_text("not a plist")
    result = launchd.svc_scan(directories=(tmp_path,))
    assert len(result["invalid"]) == 1
    assert "org.demo.bad.plist" in log_path().read_text()


def test_delayed_quit_success_is_not_a_failed_action(fake_runner, monkeypatch):
    from travel_mode.actions.apps import QuitApp
    from travel_mode.actions.base import Context
    from travel_mode.actions.executor import Executor

    elapsed = [0.0]
    monkeypatch.setattr(apps.time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    monkeypatch.setattr(apps, "app_running", lambda path: elapsed[0] < 3)
    monkeypatch.setattr(apps, "bundle_alive", lambda path: elapsed[0] < 3)
    monkeypatch.setattr(apps, "app_bid", lambda path: "org.demo.delayed")
    fake_runner.responses[("osascript",)] = CmdResult("", "User canceled (-128)", 1)
    snapshot = state.new_state()
    result = Executor(Context(snapshot, state.save_state)).execute(QuitApp("/fixture/Delayed.app", "Delayed"))
    assert result and not snapshot.get("action_failures")
    assert snapshot["session"]["actions"] == dict(attempted=1, succeeded=1, failed=0)


@pytest.mark.parametrize("seen,source", [(True, "Battery Power"), (False, "AC Power")])
def test_status_does_not_estimate_after_external_power(seen, source, fake_runner, monkeypatch):
    from travel_mode import sessions
    from travel_mode.system import battery

    snapshot = state.new_state()
    sessions.begin(snapshot)
    snapshot["session"]["external_seen"] = seen
    snapshot["session"]["start"]["telemetry"] = dict(system_count=1, system_sum_mw=1)
    monkeypatch.setattr(status, "load_state", lambda: snapshot)
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult(source, "", 0)
    monkeypatch.setattr(
        battery, "read", lambda **kwargs: pytest.fail("no estimate when external power was observed")
    )
    result = status.data()
    assert result["battery"]["remaining_minutes"] is None


def test_check_verbose_retains_technical_evidence(fake_runner, capsys):
    from travel_mode.flows.check import mode_check

    assert mode_check() == 0
    plain = capsys.readouterr().out
    assert "ps %CPU" not in plain and "lowpowermode=" not in plain
    assert mode_check(verbose=True) == 0
    verbose = capsys.readouterr().out
    assert "ps %CPU" in verbose and "lowpowermode=" in verbose


def test_app_row_error_does_not_offer_refusal_controls():
    from travel_mode.tui.view_models import app_row, refused

    assert not refused(dict(quit_status="error", stderr="AppleEvent denied (-1743)"))
    row = app_row(dict(name="Demo", path="/fixture/Demo.app", defact="quit"), state="error", failure="denied")
    assert "retry_quit" not in row["operations"] and "force_quit" not in row["operations"]


@pytest.mark.parametrize("invalid", [False, True])
def test_configured_agent_read_error_is_an_action_failure_not_internal_error(tmp_path, invalid):
    from travel_mode.actions.apps import QuitDaemonApp
    from travel_mode.actions.base import Context
    from travel_mode.actions.executor import Executor
    from travel_mode.config import DaemonApp
    from travel_mode.failures import log_path

    path = tmp_path / "org.demo.agent.plist"
    if invalid:
        path.write_text("broken plist")
    snapshot = state.new_state()
    result = Executor(Context(snapshot, state.save_state)).execute(
        QuitDaemonApp(DaemonApp("Demo", user_agents=(str(path),)), snapshot, state.save_state)
    )
    assert not result[1]
    assert snapshot["action_failures"][-1]["kind"] == "action"
    assert "Traceback" not in log_path().read_text()


@pytest.mark.parametrize("helpers", [False, True])
def test_delayed_daemon_app_quit_does_not_claim_force_or_fail(helpers, fake_runner, monkeypatch):
    from travel_mode import failures
    from travel_mode.actions.apps import QuitDaemonApp
    from travel_mode.actions.base import Context
    from travel_mode.actions.executor import Executor
    from travel_mode.config import DaemonApp
    from travel_mode.system import daemon_apps
    from travel_mode.system.shell import run_result

    observations = iter([True])
    monkeypatch.setattr(daemon_apps, "app_path", lambda _: "/fixture/Delayed.app")
    monkeypatch.setattr(daemon_apps, "bundle_alive", lambda _: next(observations, False))
    monkeypatch.setattr(daemon_apps, "force_kill", lambda _: pytest.fail("the main process has exited"))
    fake_runner.responses[("osascript",)] = CmdResult("", "User canceled (-128)", 1)

    def quit(*args):
        run_result(["osascript", "-e", "fixture"])
        return apps.QuitResult("exited", True, 1, stderr="User canceled (-128)", helpers_remaining=helpers)

    monkeypatch.setattr(daemon_apps, "grace_quit", quit)
    snapshot = state.new_state()
    messages = []
    with failures.reporter(lambda *row: messages.append(row)):
        result = Executor(Context(snapshot, state.save_state)).execute(
            QuitDaemonApp(DaemonApp("Delayed"), snapshot, state.save_state)
        )
    assert result[1] and not snapshot.get("action_failures")
    assert snapshot["session"]["actions"]["failed"] == 0
    assert not messages


def test_long_assertion_source_claim_requires_observed_power_transition():
    holder = pmset.sleep_blocking_assertions(
        ['pid 42(Remote): 121:00:00 PreventUserIdleSystemSleep named: "remote"']
    )[0]
    from travel_mode.i18n import t

    plain = health_view.assertion_text(holder, session_seconds=7200, verbose=True)
    assert t("health_view.pre_session") in plain
    assert t("health_view.long_observation") not in plain
    both = health_view.assertion_text(holder, session_seconds=7200, power_transition=True, verbose=True)
    assert t("health_view.long_observation") in both
    older = health_view.assertion_text(
        holder, session_seconds=200 * 3600, power_transition=True, verbose=True
    )
    assert t("health_view.pre_session") not in older


def test_health_view_uses_active_session_scenario_when_configuration_asks():
    row = dict(
        id="assertions",
        title="assertions",
        status="hit",
        error=None,
        actions=[],
        impact="",
        manual="",
        evidence=['pid 42(Remote): 121:00:00 PreventUserIdleSystemSleep named: "remote"'],
    )
    report = dict(
        checks=[row], assertion_context=dict(scenario="away", session_seconds=7200, power_transition=True)
    )
    assert health_view.presentation(report, scenario="ask")[0]["advisory"]
    report["assertion_context"]["scenario"] = "work"
    assert not health_view.presentation(report, scenario="ask")[0]["advisory"]


def test_conflict_start_time_uses_local_timezone(monkeypatch):
    import os
    import time

    from travel_mode.session_conflict import describe

    original = os.environ["TZ"]
    monkeypatch.setenv("TZ", "Etc/GMT+7")
    time.tzset()
    try:
        snapshot = state.new_state()
        snapshot.update(scenario="away", session=dict(started_at="2026-10-17T23:26:00+00:00"))
        assert "16:26" in describe(snapshot)
    finally:
        os.environ["TZ"] = original
        time.tzset()

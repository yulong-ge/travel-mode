"""Session math, incomplete intervals, bounded local history and retry receipts."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import validate

from travel_mode import sessions, state
from travel_mode.cli import main
from travel_mode.flows.report import parse_darkwake_reasons, parse_sleep_segments
from travel_mode.system.shell import CmdResult


def observation(at, percent, mah, *, external=False):
    return dict(
        at=at,
        external=external,
        charging=False,
        percent=percent,
        remaining_mah=mah,
        voltage_mv=12000,
        errors=[],
    )


@pytest.fixture
def session_machine(monkeypatch, fake_runner):
    start = observation("2026-10-15T10:00:00+00:00", 80, 4000)
    end = observation("2026-10-15T12:00:00+00:00", 60, 3000)
    monkeypatch.setattr(sessions, "observe", lambda: copy.deepcopy(start))
    monkeypatch.setattr(sessions, "uuid4", lambda: "fixture-session")
    st = state.new_state()
    st["scenario"] = "work"
    sessions.begin(st)
    monkeypatch.setattr(sessions, "observe", lambda: copy.deepcopy(end))
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult(
        "2026-10-15 09:59:00 Power Using Batt Charge: 80\n", "", 0
    )
    return st, end


def test_session_capacity_math_and_window_source_coverage(session_machine):
    st, _ = session_machine
    report = sessions.build_report(st, 2, 0)
    assert report["duration_seconds"] == 7200
    assert report["percent_drop"] == 20 and report["mah_drop"] == 1000
    assert report["average_watts"] == 6 and report["average_scope"] == "session"
    sessions.validate_report(report)
    validate(
        {"version": 1, "limit": 50, "sessions": [{**report, "comparison": None}]},
        json.loads(Path("docs/json-schema/history.schema.json").read_text()),
    )


def test_legacy_read_worker_failure_does_not_count_or_appear_as_failed_action(session_machine):
    from travel_mode import failures
    from travel_mode.i18n import t

    st, _ = session_machine
    st["session"]["actions"] = dict(attempted=26, succeeded=25, failed=1)
    row = failures.record(st, t("failure.worker", group="battery"), "old read error", kind="internal")
    report = sessions.build_report(st, 0, 0)
    assert report["actions"] == dict(attempted=25, succeeded=25, failed=0)
    text = "\n".join(sessions.describe(report))
    assert t("report.actions", **report["actions"]) in text
    assert row["action"] not in text
    assert row["action"] in "\n".join(sessions.describe(report, verbose=True))
    # Reading archived reports predating this fix applies the same projection.
    legacy = {**report, "actions": st["session"]["actions"], "failures": [row]}
    assert t("report.actions", **report["actions"]) in "\n".join(sessions.describe(legacy))
    assert row["action"] in failures.log_path().read_text()


def test_power_log_timeout_is_read_once_with_longer_budget_and_restore_still_finishes(
    session_machine, monkeypatch
):
    from travel_mode.flows.restore import mode_off
    from travel_mode.system import shell

    st, _ = session_machine
    calls = []
    runner = shell.get_runner()

    def timed_out(command, timeout=None, interactive=False):
        if command == ["pmset", "-g", "log"]:
            calls.append(timeout)
            return CmdResult("", "timeout", 124)
        return runner(command, timeout, interactive)

    shell.set_runner(timed_out)
    state.save_state(st)
    assert mode_off() == 0 and not state._path().exists()
    assert calls == [10]
    report = sessions.read_history()[-1]
    text = "\n".join(sessions.describe(report))
    assert "无法读取电源记录" in text and "睡眠记录读取超时" in text
    assert report["power_status"] == "unknown"


def test_old_measurement_uses_noise_threshold_in_session_and_report(session_machine):
    from travel_mode.measurement import comparison_description
    from travel_mode.tui.session_view import session_view

    st, _ = session_machine
    st["measurement"] = dict(
        before=dict(available=True, average_watts=8.82, interval_count=1, capacity_source="BatteryData"),
        after=dict(available=True, average_watts=8.54, interval_count=1, capacity_source="BatteryData"),
        saved_watts=0.28,
        extra_hours=0.18,
    )
    expected = "执行前 8.8 W → 执行后 8.5 W，差异在测量误差范围内（单次数据，精度较低）。"
    assert comparison_description(st["measurement"]) == expected
    assert expected in session_view(st)["lines"]
    text = "\n".join(sessions.describe(sessions.build_report(st, 0, 0)))
    assert expected in text
    assert "0.28" not in text and "BatteryData" not in text and "0.18" not in text


@pytest.mark.parametrize("source", ["observed", "log", "capacity_increase", "read_error", "missing"])
def test_incomplete_or_charging_intervals_never_synthesize_discharge(source, session_machine, fake_runner):
    st, end = session_machine
    if source == "observed":
        st["session"]["external_seen"] = True
    if source == "log":
        fake_runner.responses[("pmset", "-g", "log")] = CmdResult(
            "2026-10-15 09:59:00 Power Using Batt\n2026-10-15 11:00:00 Using AC\n"
            "2026-10-15 11:30:00 Using Batt",
            "",
            0,
        )
    if source == "capacity_increase":
        end["remaining_mah"] = 4500
    if source == "read_error":
        fake_runner.responses[("pmset", "-g", "log")] = CmdResult("", "timeout", 124)
    if source == "missing":
        end["remaining_mah"] = None
        end["errors"] = ["missing capacity"]
    report = sessions.build_report(st, 0, 0)
    assert report["average_watts"] is None
    assert report["percent_drop"] is None and report["mah_drop"] is None
    assert "无法" in "\n".join(sessions.describe(report)) or report["errors"]


def test_away_log_filters_session_bounds_and_does_not_bridge_partial_sleep(session_machine, fake_runner):
    st, _ = session_machine
    st["scenario"] = "away"
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult(
        """2026-10-15 09:00:00 Entering Sleep Using Batt Charge: 90
2026-10-15 09:30:00 Maintenance Sleep Using Batt Charge: 85
2026-10-15 09:31:00 Assertions Summary Using Batt Charge: 85
2026-10-15 10:10:00 Entering Sleep Using Batt Charge: 80
2026-10-15 11:10:00 Maintenance Sleep Using Batt Charge: 78
2026-10-15 11:11:00 Assertions Summary Using Batt Charge: 78
2026-10-15 11:00:00 DarkWake BATT due to RTC/Maintenance
2026-10-15 12:01:00 DarkWake BATT due to Network
""",
        "",
        0,
    )
    r = sessions.build_report(st, 3, 0)
    assert r["away"]["percent_drop"] == 2
    assert r["away"]["darkwake_count"] == 1 and r["away"]["reasons"] == {"RTC": 1}
    assert len(r["away"]["segments"]) == 1


def test_legacy_snapshot_reports_missing_start_without_fabricated_counts(monkeypatch):
    monkeypatch.setattr(sessions, "observe", lambda: observation("2026-10-15T12:00:00+00:00", 60, 3000))
    r = sessions.build_report(state.new_state(), 1, 0)
    assert r["start"] is r["duration_seconds"] is r["actions"] is None
    assert r["errors"] and r["average_watts"] is None


def test_endpoint_observation_preserves_raw_capacity_on_external_power(fake_runner):
    row = sessions.observe()
    assert row["external"] is True and row["charging"] is False
    assert row["percent"] == 87 and row["remaining_mah"] == 4000 and row["voltage_mv"] == 12000
    assert row["errors"] == []
    assert fake_runner.calls == [("ioreg", "-a", "-rn", "AppleSmartBattery"), ("pmset", "-g", "batt")]


@pytest.mark.parametrize("output", ["", '"ExternalConnected" = Maybe\n"Voltage" = 0\n'])
def test_missing_or_malformed_endpoint_is_explicit_without_synthetic_capacity(output, fake_runner):
    fake_runner.responses[("ioreg", "-a", "-rn", "AppleSmartBattery")] = CmdResult(output, "", 0)
    fake_runner.responses[("pmset", "-g", "batt")] = CmdResult("", "read denied", 1)
    row = sessions.observe()
    assert row["percent"] is row["remaining_mah"] is row["voltage_mv"] is None
    assert row["external"] is None and row["errors"]
    assert "read denied" in " ".join(row["errors"])


def test_history_retention_idempotency_and_same_scenario_comparison(session_machine, tmp_paths):
    st, _ = session_machine
    r = sessions.build_report(st, 1, 0)
    for i in range(4):
        row = {**r, "id": str(i), "average_watts": float(i + 2)}
        sessions.append_history(row, 3)
        sessions.append_history(row, 3)
    rows = sessions.read_history()
    assert [row["id"] for row in rows] == ["1", "2", "3"]
    assert sessions.comparison(rows[-1], rows) == {"count": 2, "average_watts": 3.5, "difference_watts": -1.5}
    assert sessions.comparison({**rows[-1], "scenario": "away"}, rows) is None
    with pytest.raises(state.StateError, match="conflicting"):
        sessions.append_history({**rows[-1], "average_watts": 100}, 3)


@pytest.mark.parametrize("text", ["{broken}", "{}", "null", '{"version":99}'])
def test_history_corruption_reports_file_and_line(text, tmp_paths):
    path = tmp_paths / "history.jsonl"
    path.write_text(text + "\n")
    with pytest.raises(state.StateError, match=r"history\.jsonl: line 1"):
        sessions.read_history()


def test_history_write_failure_keeps_report_checkpoint_for_retry(session_machine, monkeypatch):
    st, _ = session_machine
    state.save_state(st)
    original = sessions.append_history

    def fail(*args):
        raise state.StateError("history denied")

    monkeypatch.setattr(sessions, "append_history", fail)
    with pytest.raises(state.StateError, match="denied"):
        sessions.finish(st, 4, 0)
    saved = state.load_state()
    assert saved["session_report_pending"]["restore"]["restored"] == 4
    monkeypatch.setattr(sessions, "append_history", original)
    monkeypatch.setattr(
        sessions, "observe", lambda: pytest.fail("retry must not take a different end reading")
    )
    report = sessions.finish(saved, 0, 0)
    assert report["restore"]["restored"] == 4 and len(sessions.read_history()) == 1


def test_history_cli_json_limit_and_no_observation(session_machine, darwin_platform, monkeypatch, capsys):
    st, _ = session_machine
    r = sessions.build_report(st, 1, 0)
    sessions.append_history(r, 50)
    monkeypatch.setattr(sessions, "observe", lambda: pytest.fail("history is a local file read"))
    assert main(["history", "--json", "--limit", "1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sessions"][0]["id"] == "fixture-session" and payload["limit"] == 1
    assert main(["history"]) == 0
    from travel_mode.i18n import t

    assert t("session.heading", scenario=t("scenario.work")) in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["history", "--limit", "0"])


def test_resurrection_and_external_observations_survive_roundtrip(session_machine):
    st, _ = session_machine
    st["confirmed_pauses"] = [dict(kind="app", path="/Applications/Worker.app")]
    state.save_state(st)
    row = dict(kind="app", name="Worker", path="/Applications/Worker.app")
    sessions.note(st, [row, row], external=True)
    loaded = state.load_state()
    assert loaded["session"]["resurrected"] == [row] and loaded["session"]["external_seen"]


def test_observation_never_recreates_state_deleted_by_off(session_machine):
    st, _ = session_machine
    with pytest.raises(state.StateError, match=r"session\.id"):
        sessions.note(st, external=True)
    assert not state._path().exists()


def test_action_counts_use_executor_and_restore_is_separate(session_machine):
    from travel_mode.actions.base import Action, Context
    from travel_mode.actions.executor import Executor

    class Dummy(Action):
        def apply(self, ctx):
            return True

    st, _ = session_machine
    Executor(Context(st, state.save_state)).execute(Dummy())
    assert state.load_state()["session"]["actions"] == {"attempted": 1, "succeeded": 1, "failed": 0}


def test_session_config_retention_is_strict(tmp_paths):
    from travel_mode.config import ConfigError, load_config

    p = tmp_paths / "config.toml"
    p.write_text("[history]\nretain=0\n")
    with pytest.raises(ConfigError, match=r"history\.retain"):
        load_config()


def test_measurement_scope_and_source_change_are_kept_separate(session_machine, fake_runner):
    st, _ = session_machine

    def window(watts):
        return dict(
            available=True,
            reason=None,
            samples=10,
            seconds=20,
            average_watts=watts,
            remaining_wh=36,
            remaining_hours=36 / watts,
            macos={"raw": "fixture", "minutes": None},
        )

    st["session"]["start"]["external"] = None
    st["measurement"] = dict(before=window(12), after=window(6), saved_watts=6, extra_hours=3)
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult("", "", 0)
    report = sessions.build_report(st, 0, 0)
    assert report["average_watts"] == 6 and report["average_scope"] == "after_window"
    sessions.validate_report(report)
    assert sessions.comparison(report, [{**report, "id": "other", "average_scope": "session"}]) is None
    st["measurement"]["after"] = {
        "available": False,
        "reason": "external_power",
        "samples": 0,
        "macos": {"raw": "fixture", "minutes": None},
    }
    st["measurement"]["saved_watts"] = st["measurement"]["extra_hours"] = None
    report = sessions.build_report(st, 0, 0)
    assert report["power_status"] == "external_seen" and report["average_watts"] is None
    from travel_mode.i18n import t

    text = "\n".join(sessions.describe(report))
    assert t("report.before", watts=12) in text and "12.00" in text


def test_failed_actions_are_counted_and_pending_report_blocks_more_mutations(session_machine):
    from travel_mode.actions.base import Action, Context
    from travel_mode.actions.executor import Executor

    class Broken(Action):
        def apply(self, ctx):
            raise RuntimeError("action failed")

    st, _ = session_machine
    executor = Executor(Context(st, state.save_state))
    with pytest.raises(RuntimeError, match="action failed"):
        executor.execute(Broken())
    assert state.load_state()["session"]["actions"] == dict(attempted=1, succeeded=0, failed=1)
    st["session_report_pending"] = sessions.build_report(st, 0, 0)
    with pytest.raises(state.StateError):
        executor.execute(Broken())
    assert st["session"]["actions"]["attempted"] == 1


def test_partial_restore_counts_accumulate_before_final_archive(session_machine, fake_runner):
    from travel_mode.flows.restore import mode_off
    from travel_mode.system.shell import set_runner

    st, _ = session_machine
    st["wifi_off"] = "en0"
    st["bt_off"] = "1"
    state.save_state(st)
    fake_runner.responses[("sudo", "networksetup")] = CmdResult("", "denied", 1)
    fake_runner.responses[("blueutil", "-p", "1")] = CmdResult("", "", 0)
    assert mode_off() == 1
    assert sessions.read_history() == []
    saved = state.load_state()
    assert saved["session"]["restored"] == 1 and saved["session"]["restore_failures"] == 1
    fake_runner.responses[("sudo", "networksetup")] = CmdResult("", "", 0)
    set_runner(fake_runner)
    assert mode_off() == 0
    report = sessions.read_history()[0]
    assert report["restore"] == dict(restored=2, failed=0, failed_attempts=1)


LOG = """2026-01-01 01:00:00 Using Batt: Charge: 90; Entering Sleep
2026-01-01 02:00:00 Using Batt: Charge: 87; Clamshell Sleep
2026-01-01 03:00:00 Using Batt: Charge: 87; Assertions Summary
2026-01-01 04:00:00 DarkWake due to RTC/Maintenance BATT
2026-01-01 05:00:00 DarkWake due to TCPKeepAlive BATT
"""


def test_parse_sleep_segments():
    assert parse_sleep_segments(LOG)[0]["drain"] == 3


def test_parse_darkwake_reasons():
    assert parse_darkwake_reasons(LOG) == {"RTC": 1, "TCPKeepAlive": 1}

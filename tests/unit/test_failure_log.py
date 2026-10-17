"""Failure receipts are bounded, private, durable and shared with reports."""

import json

import pytest

from travel_mode import failures, sessions, state
from travel_mode.actions.apps import QuitApp
from travel_mode.actions.base import Action, Context
from travel_mode.actions.executor import Executor
from travel_mode.system import apps


def test_log_rotates_with_private_permissions_and_preserves_traceback(tmp_paths, monkeypatch):
    monkeypatch.setattr(failures, "MAX_LOG_BYTES", 2048)
    st = state.new_state()
    for index in range(20):
        try:
            raise LookupError("fixture bug " + str(index))
        except LookupError as exc:
            failures.record(st, "fixture action", str(exc), error=exc)
    log = failures.log_path()
    assert log.stat().st_size <= 2048
    rotated = log.with_suffix(".log.1")
    assert rotated.exists() and rotated.stat().st_size <= 2048
    for path in (log, rotated):
        assert path.stat().st_mode & 0o777 == 0o600
        for line in path.read_text().splitlines():
            row = json.loads(line)
            assert "Traceback" in row["traceback"] and "LookupError" in row["traceback"]
            assert "environment" not in row


def test_failure_receipts_round_trip_and_report_unknown_rc_without_python_none(tmp_paths):
    st = state.new_state()
    failures.record(st, "fixture action", "fixture refusal")
    state.save_state(st)
    loaded = state.load_state()
    assert loaded["action_failures"] == st["action_failures"]
    report = sessions.build_report(loaded, 0, 0)
    sessions.validate_report(report)
    sessions.append_history(report, 50)
    assert sessions.read_history()[0]["failures"] == loaded["action_failures"]
    assert "None" not in "\n".join(sessions.describe(report))


def test_receipt_limits_reject_invalid_data_and_bound_saved_details(tmp_paths):
    st = state.new_state()
    for _ in range(55):
        failures.record(
            st,
            "fixture action",
            "x" * 9000,
            [dict(command=["fixture"] * 100, rc=2, stdout="x" * 9000, stderr="y" * 9000)],
        )
    assert len(st["action_failures"]) == 50
    failures.validate(st["action_failures"])
    state.save_state(st)
    assert state.load_state()["action_failures"] == st["action_failures"]
    bad = {**st["action_failures"][0], "rc": "not a code"}
    with pytest.raises(ValueError):
        failures.validate([bad])


def test_quit_action_counts_refusal_and_journals_before_event(monkeypatch):
    st = state.new_state()
    path = "/fixture/Refusing.app"

    def reject(*args):
        from travel_mode.actions.journal import record_change

        record_change("quit_apps", path)
        assert path in state.load_state()["quit_apps"]
        return apps.QuitResult("refused", False, 1, "", "User canceled. (-128)", ["osascript"])

    monkeypatch.setattr(apps, "grace_quit", reject)
    assert not Executor(Context(st, state.save_state)).execute(QuitApp(path, "Refusing"))
    snapshot = state.load_state()
    assert snapshot["session"]["actions"] == dict(attempted=1, succeeded=0, failed=1)
    assert snapshot["action_failures"]


def test_unexpected_action_error_preserves_original_exception_and_traceback():
    class Broken(Action):
        def apply(self, ctx):
            raise KeyError("fixture missing invariant")

    st = state.new_state()
    with pytest.raises(KeyError, match="fixture missing invariant"):
        Executor(Context(st, state.save_state)).execute(Broken())
    assert state.load_state()["session"]["actions"]["failed"] == 1
    log = json.loads(failures.log_path().read_text().splitlines()[-1])
    assert log["kind"] == "internal" and "KeyError" in log["traceback"]


def test_successful_retry_retains_failure_and_marks_it_recovered(monkeypatch):
    st = state.new_state()
    executor = Executor(Context(st, state.save_state))
    path = "/fixture/Refusing.app"
    monkeypatch.setattr(apps, "grace_quit", lambda *_: apps.QuitResult("refused", False, 1, stderr="(-128)"))
    assert not executor.execute(QuitApp(path, "Refusing"))
    monkeypatch.setattr(apps, "grace_quit", lambda *_: apps.QuitResult("exited", True))
    assert executor.execute(QuitApp(path, "Refusing"))
    saved = state.load_state()
    assert saved["action_failures"][0]["recovered"] is True
    assert saved["session"]["actions"] == dict(attempted=2, succeeded=1, failed=1)


def test_long_traceback_is_complete_and_oversized_record_fails_explicitly(monkeypatch):
    detail = "fixture bug " + "x" * 20000
    try:
        raise LookupError(detail)
    except LookupError as error:
        failures.record(None, "fixture", "bug", error=error)
    logged = json.loads(failures.log_path().read_text().splitlines()[-1])
    assert detail in logged["traceback"]
    before = failures.log_path().read_bytes()
    monkeypatch.setattr(failures, "MAX_LOG_BYTES", 64)
    with pytest.raises(state.StateError, match="64"):
        failures.record(None, "fixture", "cannot fit")
    assert failures.log_path().read_bytes() == before

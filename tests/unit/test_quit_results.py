"""Apple Event failures are observations, verified against live process identity."""

import pytest

from travel_mode.system import apps
from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize(
    "rc,detail,status",
    [
        (1, "execution error: User canceled. (-128)", "refused"),
        (1, "execution error: AppleEvent timed out. (-1712)", "timeout"),
        (124, "timeout", "timeout"),
        (1, "execution error: Application is not running. (-600)", "not_running"),
        (2, "execution error: denied (-1743)", "error"),
    ],
)
@pytest.mark.parametrize("alive", [False, True])
def test_quit_result_classifies_and_rechecks_processes(rc, detail, status, alive, monkeypatch, fake_runner):
    observations = iter((True, alive))
    monkeypatch.setattr(apps, "app_running", lambda path: next(observations))
    monkeypatch.setattr(apps, "app_bid", lambda path: "org.demo.refusing")
    fake_runner.responses[("osascript",)] = CmdResult("fixture stdout", detail, rc)
    result = apps.grace_quit("/fixture/Refusing.app", "Refusing")
    assert result.status == status
    assert bool(result) is not alive and result.exited is not alive
    assert result.rc == rc and result.stderr == detail and result.stdout == "fixture stdout"
    assert result.command[0] == "osascript"


def test_already_gone_is_success_without_sending_event(monkeypatch, fake_runner):
    monkeypatch.setattr(apps, "app_running", lambda path: False)
    result = apps.grace_quit("/fixture/Gone.app", "Gone")
    assert result and result.status == "not_running" and result.command == []
    assert fake_runner.calls == []


def test_successful_quit_rechecks_and_timeout_does_not_sleep(monkeypatch, fake_runner):
    observations = iter((True, False))
    monkeypatch.setattr(apps, "app_running", lambda path: next(observations))
    monkeypatch.setattr(apps, "app_bid", lambda path: "org.demo.quitting")
    fake_runner.responses[("osascript",)] = CmdResult("", "", 0)
    monkeypatch.setattr(apps.time, "sleep", lambda _: pytest.fail("already exited; no sleep"))
    result = apps.grace_quit("/fixture/Quitting.app", "Quitting")
    assert result and result.status == "exited"


@pytest.mark.parametrize(
    "detail,rc",
    [
        ("User canceled. (-128)", 1),
        ("Timed out. (-1712)", 1),
        ("timeout", 124),
        ("Not running. (-600)", 1),
        ("denied (-1743)", 2),
    ],
)
@pytest.mark.parametrize("alive", [False, True])
def test_quit_action_records_each_external_outcome_without_raising(
    detail, rc, alive, monkeypatch, fake_runner
):
    from travel_mode import state
    from travel_mode.actions.apps import QuitApp
    from travel_mode.actions.base import Context
    from travel_mode.actions.executor import Executor

    observations = iter((True, alive))
    monkeypatch.setattr(apps, "app_running", lambda path: next(observations))
    monkeypatch.setattr(apps, "app_bid", lambda path: "org.demo.refusing")
    fake_runner.responses[("osascript",)] = CmdResult("fixture stdout", detail, rc)
    st = state.new_state()
    result = Executor(Context(st, state.save_state)).execute(QuitApp("/fixture/Refusing.app", "Refusing"))
    assert bool(result) is not alive
    loaded = state.load_state()
    assert loaded["quit_apps"] == ["/fixture/Refusing.app"]
    assert loaded["session"]["actions"] == dict(attempted=1, succeeded=int(not alive), failed=int(alive))
    if alive:
        row = loaded["action_failures"][-1]
        assert row["rc"] == rc and row["stderr"] == detail and row["stdout"] == "fixture stdout"
        assert row["command"][0] == "osascript"


def test_quit_never_uses_cached_discovery_to_verify_exit(monkeypatch, fake_runner):
    from travel_mode.system.app_snapshot import AppSnapshot, current_snapshot, snapshot_scope
    from travel_mode.system.shell import set_runner

    path = "/fixture/Fresh.app"
    query = ["ps", "-axo", "pid=,ppid=,stat=,lstart=,comm="]
    running = f"910001 1 S Thu Oct 15 12:00:00 2026 {path}/Contents/MacOS/Fresh"
    # Initial observation, fresh protection check, and post-quit verification.
    observations = iter((running, running, ""))
    fake_runner.responses[("osascript",)] = CmdResult("", "", 0)

    def runner(command, timeout=None, interactive=False):
        if command == query:
            fake_runner.calls.append(tuple(command))
            return CmdResult(next(observations), "", 0)
        return fake_runner(command, timeout, interactive)

    set_runner(runner)
    monkeypatch.setattr(apps, "app_bid", lambda path: "org.demo.fresh")
    cached = AppSnapshot(None, ())
    with snapshot_scope(cached):
        assert not apps.app_running(path)
        assert apps.grace_quit(path, "Fresh")
        assert current_snapshot() is cached
    assert sum(command[:1] == ("osascript",) for command in fake_runner.calls) == 1
    assert fake_runner.calls.count(tuple(query)) == 3


@pytest.mark.parametrize("force_ok", [True, False])
def test_quit_fallback_has_one_final_outcome_and_preserves_both_errors(monkeypatch, fake_runner, force_ok):
    import json

    from travel_mode import failures, sessions, state
    from travel_mode.actions.apps import QuitApp
    from travel_mode.actions.base import Context
    from travel_mode.actions.executor import Executor

    st = state.new_state()
    st["scenario"] = "away"
    normal = apps.QuitResult("refused", False, 1, stderr="User canceled. (-128)")
    monkeypatch.setattr(apps, "grace_quit", lambda *_: normal)

    def force(_):
        if not force_ok:
            raise failures.CommandError(["kill", "-TERM", "910001"], 2, "fixture signal denied")
        return True

    monkeypatch.setattr(apps, "force_kill", force)
    messages = []
    with failures.reporter(lambda kind, text: messages.append((kind, text))), failures.batch():
        result = Executor(Context(st, state.save_state)).execute(
            QuitApp("/fixture/Refusing.app", "Refusing", fallback=True)
        )
    assert bool(result) is force_ok
    loaded = state.load_state()
    assert loaded["session"]["actions"]["failed"] == int(not force_ok)
    report = sessions.build_report(loaded, 0, 0)
    if force_ok:
        assert not loaded.get("action_failures") and not report.get("failures")
        assert loaded["session"]["actions"] == dict(attempted=2, succeeded=2, failed=0)
        assert len([row for row in messages if row[0] == "warn"]) == 1
        assert not any(kind == "bad" for kind, _ in messages)
        assert "retry" not in messages[0][1].lower()
        assert json.loads(failures.log_path().read_text().splitlines()[-1])["kind"] == "warning"
    else:
        assert loaded["session"]["actions"] == dict(attempted=1, succeeded=0, failed=1)
        assert len(loaded["action_failures"]) == 1
        reason = loaded["action_failures"][0]["reason"]
        assert "(-128)" in reason and "fixture signal denied" in reason
        assert loaded["action_failures"][0]["rc"] == 2
        assert len(report["failures"]) == 1

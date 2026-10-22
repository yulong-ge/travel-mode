"""Every stage and emergency rollback are exercised using strict command doubles."""

from pathlib import Path

import pytest

from e2e_harness.conftest import e2e
from travel_mode.system import shell


def test_preflight_refuses_active_user_state_and_escaped_paths(sandbox, tmp_path):
    harness, _, machine = sandbox
    harness.real_state.write_text("active")
    with pytest.raises(RuntimeError, match="Active user state"):
        harness.run()
    assert not machine.calls
    harness.real_state.unlink()
    with pytest.raises(ValueError, match="escapes"):
        e2e.preflight(harness.root, tmp_path / "escaped.json", harness.real_state)
    with pytest.raises(ValueError, match="dedicated temporary"):
        e2e.preflight(Path("/non-temporary"), tmp_path / "state", harness.real_state)
    assert not machine.calls


def test_full_harness_fake_roundtrip(sandbox, capsys):
    harness, guard, machine = sandbox
    before = machine.power_text()
    harness.run()
    assert machine.power_text() == before
    assert list(machine.rows) == [77]
    assert not machine.loaded
    assert not harness.root.exists()
    assert not guard.tripped
    assert "PASS: local e2e" in capsys.readouterr().out


@pytest.mark.parametrize(
    "stage",
    [
        "fixtures",
        "started",
        "agent",
        "quit",
        "freeze",
        "unload",
        "off",
        "power-before",
        "power-applied",
        "power-off",
    ],
)
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_cleanup_runs_after_each_stage_error(stage, error_type, sandbox):
    harness, _, machine = sandbox
    before = machine.power_text()
    previous = shell.get_runner()

    def fail(name):
        if stage == name:
            raise error_type(f"injected {stage}")

    harness.checkpoint = fail
    with pytest.raises(error_type, match=f"injected {stage}"):
        harness.run()
    assert machine.power_text() == before
    assert not machine.loaded
    assert list(machine.rows) == [77]
    assert not harness.root.exists()
    assert shell.get_runner() is previous


def test_power_skipped_without_pmset_only_permission(sandbox, capsys):
    harness, _, machine = sandbox
    machine.permission = False
    harness.run()
    assert not any(cmd[:4] == ("sudo", "-n", "/usr/bin/pmset", "-b") for cmd in machine.calls)
    assert "SKIP power" in capsys.readouterr().out


def test_emergency_rollback_after_product_off_failure(sandbox, monkeypatch, capsys):
    harness, _, machine = sandbox
    before = machine.power_text()
    original = e2e.run_restore

    def restore(st, emit):
        if st.get("batt_prof"):
            return 0, 1
        return original(st, emit)

    monkeypatch.setattr(e2e, "run_restore", restore)
    with pytest.raises(AssertionError, match="no failed items"):
        harness.run()
    assert machine.power_text() == before
    assert "EMERGENCY POWER ROLLBACK" in capsys.readouterr().out
    assert not harness.root.exists()


def test_failed_emergency_rollback_is_loud_and_nonzero(sandbox, capsys):
    harness, _, machine = sandbox

    def fail(stage):
        if stage == "power-applied":
            machine.fail_restore = True
            raise RuntimeError("trigger rollback")

    harness.checkpoint = fail
    with pytest.raises(e2e.CleanupError, match="restore denied") as caught:
        harness.run()
    assert isinstance(caught.value.__context__, RuntimeError)
    assert "CLEANUP ERROR" in capsys.readouterr().err
    assert not machine.loaded
    assert list(machine.rows) == [77]
    assert not harness.root.exists()


def test_guard_failure_aborts_and_cleans(sandbox):
    harness, guard, machine = sandbox

    def violate(stage):
        if stage == "freeze":
            guard(["kill", "-STOP", "77"])

    harness.checkpoint = violate
    with pytest.raises(PermissionError, match="REFUSED"):
        harness.run()
    assert list(machine.rows) == [77]
    assert not harness.root.exists()


def test_plan_executes_no_commands(sandbox, monkeypatch, capsys):
    _, _, machine = sandbox
    monkeypatch.setattr(e2e, "LocalBackend", lambda *_: pytest.fail("plan created backend"))
    monkeypatch.setattr(e2e, "create_fixtures", lambda *_: pytest.fail("plan created fixtures"))
    monkeypatch.setattr(shell, "subprocess_runner", lambda *_: pytest.fail("plan executed command"))
    assert e2e.main(["--plan"]) == 0
    output = capsys.readouterr().out
    assert "PLAN ONLY" in output and "-STOP" in output and "EMERGENCY" not in output
    assert "emergency rollback" in output
    assert machine.calls == []


@pytest.mark.e2e
def test_real_local_e2e():
    """Manual opt-in only. Never selected by the default pytest invocation."""
    assert e2e.main([]) == 0


def test_powermode_variant_roundtrip(sandbox):
    harness, _, machine = sandbox
    machine.battery["powermode"] = machine.battery.pop("lowpowermode")
    original = machine.power_text()
    harness.run()
    assert machine.power_text() == original
    assert any("powermode" in cmd for cmd in machine.calls if "-b" in cmd)


def test_cleanup_failure_still_attempts_independent_cleanup(sandbox, monkeypatch):
    harness, _, machine = sandbox
    original = harness.command

    def fail(cmd):
        if cmd[:2] == ["launchctl", "unload"]:
            raise RuntimeError("injected cleanup unload failure")
        return original(cmd)

    def trigger(stage):
        if stage == "agent":
            monkeypatch.setattr(harness, "command", fail)
            raise RuntimeError("trigger cleanup")

    harness.checkpoint = trigger
    with pytest.raises(e2e.CleanupError, match="cleanup unload failure"):
        harness.run()
    assert list(machine.rows) == [77]
    assert not harness.root.exists()


def test_foreign_transition_fails_assertion_and_cleans(sandbox):
    harness, _, machine = sandbox

    def alter(stage):
        if stage == "freeze":
            _, birth, path = machine.rows[77]
            machine.rows[77] = ("T", birth, path)

    harness.checkpoint = alter
    with pytest.raises(e2e.CleanupError, match="No observed stop/resume transitions"):
        harness.run()
    assert machine.rows[77][0] == "T"  # Harness must never signal even a stopped foreign PID.
    assert not any(cmd[:1] == ("kill",) and cmd[-1] == "77" for cmd in machine.calls)


def test_unexpected_cleanup_error_does_not_skip_other_phases(sandbox, monkeypatch):
    harness, _, machine = sandbox
    original = harness.command

    def fail(cmd):
        if cmd[:2] == ["launchctl", "unload"]:
            raise TypeError("unexpected cleanup type error")
        return original(cmd)

    def trigger(stage):
        if stage == "agent":
            monkeypatch.setattr(harness, "command", fail)
            raise RuntimeError("trigger unexpected cleanup")

    harness.checkpoint = trigger
    with pytest.raises(TypeError, match="unexpected cleanup type error"):
        harness.run()
    assert list(machine.rows) == [77]
    assert not harness.root.exists()


def test_preexisting_agent_label_is_never_unloaded(sandbox):
    harness, _, machine = sandbox
    machine.loaded = True
    with pytest.raises(RuntimeError, match="already loaded"):
        harness.run()
    assert machine.loaded
    assert list(machine.rows) == [77]
    assert machine.calls == [("launchctl", "list")]
    assert not harness.root.exists()


@pytest.mark.parametrize("tcc", ["-1743 not authorized"])
def test_graceful_tcc_skip_continues_safe_power_and_cleans(sandbox, tcc, capsys):
    harness, guard, machine = sandbox
    machine.tcc = tcc
    harness.run()
    text = capsys.readouterr().out
    assert "SKIP graceful" in text and "Privacy & Security > Automation" in text
    assert "Product repaired SleepDisabled" in text
    assert not guard.tripped and list(machine.rows) == [77]
    assert any(c[0].endswith("/lsregister") for c in machine.calls)


def test_graceful_quit_has_no_signal_before_off(sandbox):
    harness, guard, machine = sandbox
    harness.run()
    from e2e_guard import QUIT_EVENT

    events = [i for i, c in enumerate(guard.commands) if c == tuple(QUIT_EVENT)]
    first = events[0]
    reopened = next(
        i for i, c in enumerate(guard.commands[first + 1 :], first + 1) if c[:3] == ("open", "-g", "-j")
    )
    assert not any(c[0] == "kill" for c in guard.commands[first:reopened])
    assert machine.sleep_disabled == "1" and machine.ac["powernap"] == machine.ac["womp"] == "1"
    assert machine.battery["hibernatemode"] == "3"


def test_thaw_skips_foreign_stopped_app_without_signalling(sandbox, capsys):
    harness, guard, machine = sandbox

    def stop_foreign(stage):
        if stage == "off":
            machine.rows[78] = (
                "T",
                "Thu Oct 15 12:00:00 2026",
                "/Applications/Other.app/Contents/MacOS/Other",
            )

    harness.checkpoint = stop_foreign
    harness.run()
    assert "SKIP thaw --all" in capsys.readouterr().out
    assert not any(c[0] == "kill" and c[-1] == "78" for c in guard.commands)


def test_all_power_domains_roll_back_after_failure(sandbox):
    harness, _, machine = sandbox
    before = machine.power_text()

    def fail(stage):
        if stage == "power-applied":
            assert machine.sleep_disabled == "0"
            assert machine.ac["powernap"] == machine.ac["womp"] == "0"
            assert machine.battery["hibernatemode"] == "25"
            raise RuntimeError("all domains dirty")

    harness.checkpoint = fail
    with pytest.raises(RuntimeError, match="all domains dirty"):
        harness.run()
    assert machine.power_text() == before and machine.sleep_disabled == "1"


def test_ui_plan_creates_no_backend_or_tmux(monkeypatch, capsys):
    import e2e_ui

    monkeypatch.setattr(e2e_ui, "UISession", lambda *_: pytest.fail("plan starts tmux"))
    monkeypatch.setattr(e2e_ui, "LocalBackend", lambda *_: pytest.fail("plan creates backend"))
    assert e2e_ui.main(["--plan"]) == 0
    assert "UI: four isolated tmux children (work/away x CLI/TUI)" in capsys.readouterr().out


def test_ui_prompt_policy_allows_only_numeric_cpu_variation():
    from e2e_ui import cpu_pattern, select_answer

    from travel_mode.i18n import t

    expected = t("impl.quit_freeze_skip", v0="Fixture (CPU <CPU>%)", v1="Y", v2="f")
    policy = {cpu_pattern(expected): ("fixture", "y")}
    actual = t("impl.quit_freeze_skip", v0="Fixture (CPU 0.7%)", v1="Y", v2="f")
    assert select_answer(actual, policy) == ("fixture", "y")
    with pytest.raises(AssertionError, match="Unexpected"):
        select_answer(actual.replace("Fixture", "User App"), policy)
    with pytest.raises(AssertionError, match="Unexpected"):
        select_answer(t("impl.turn_off_the_display_now_pmset"), policy)

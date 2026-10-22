"""Repair reuses sudo and durable intent without starting a saving session."""

import pytest

from travel_mode import cli, state
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize("existing", [False, True])
def test_sleep_repair_journals_before_command_and_preserves_active_session(
    existing, fake_runner, darwin_platform
):
    snapshot = state.new_state()
    if existing:
        snapshot.update(scenario="work", wifi_off="en0", sleepdisabled="1")
        state.save_state(snapshot)
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    seen = []

    def runner(command, timeout=None, interactive=False):
        if command == ["sudo", "pmset", "-a", "disablesleep", "0"]:
            saved = state.load_state()
            assert saved["sleepdisabled"] == "1"
            assert command in saved["pending_commands"]
            seen.append(command)
        return fake_runner(command, timeout, interactive)

    shell.set_runner(runner)
    assert cli.main(["sleep", "repair"]) == 0
    assert len(seen) == 1
    saved = state.load_state()
    if existing:
        assert saved["wifi_off"] == "en0" and saved["scenario"] == "work"
        assert saved["sleepdisabled"] == "" and saved["power_origin"]["sleepdisabled"] == "0"
    else:
        assert saved is None


def test_failed_sleep_repair_retains_uncertain_recovery_and_returns_nonzero(fake_runner, darwin_platform):
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "repair denied", 1)
    assert cli.main(["sleep", "repair"]) == 1
    saved = state.load_state()
    assert saved["sleepdisabled"] == "1" and saved["pending_commands"]
    assert not saved.get("session")


def test_sleep_repair_dry_run_does_not_write_original_state(fake_runner, darwin_platform, tmp_path):
    assert cli.main(["sleep", "repair", "--dry-run"]) == 0
    assert not (tmp_path / "state.json").exists()
    assert not any(call[0] == "sudo" and "disablesleep" in call for call in fake_runner.calls)


def test_unsupported_sleep_switch_fails_explicitly(fake_runner, darwin_platform, capsys):
    fake_runner.responses[("pmset", "-g")] = CmdResult("Currently in use:\n sleep 0\n", "", 0)
    assert cli.main(["sleep", "repair"]) == 1
    assert "SleepDisabled" in capsys.readouterr().err
    assert state.load_state() is None


def test_retry_standalone_repair_does_not_leave_an_empty_active_session(fake_runner, darwin_platform):
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "repair denied", 1)
    assert cli.main(["sleep", "repair"]) == 1
    assert state.load_state()["pending_commands"]
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    assert cli.main(["sleep", "repair"]) == 0
    assert state.load_state() is None


def test_password_not_provided_uses_normal_probe_and_leaves_no_session(fake_runner, darwin_platform):
    fake_runner.responses[("sudo", "-n", "-l")] = CmdResult("", "password required", 1)
    assert cli.main(["sleep", "repair"]) == 1
    assert any(call[:3] == ("sudo", "-n", "-l") for call in fake_runner.calls)
    assert not any(call[:2] == ("sudo", "pmset") for call in fake_runner.calls)
    assert state.load_state() is None

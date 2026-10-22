"""Exercise the shared action boundary without a CLI or TUI presenter."""

import pytest

from travel_mode import snapshots, state
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.actions.network import BluetoothOff, WifiOff
from travel_mode.actions.power import EnableSleep, SetPowerProfile
from travel_mode.system.shell import CmdResult, set_runner


def test_executor_requires_recovery_context(fake_runner):
    with pytest.raises(state.StateError):
        Executor().execute(EnableSleep())
    assert not fake_runner.calls


def test_action_sequence_persists_intent_before_each_command(fake_runner):
    snapshot, _ = snapshots.snapshot_origin()
    observed = []

    def runner(command, timeout=None, interactive=False):
        if command == ["ioreg", "-a", "-rn", "AppleSmartBattery"] or command == ["pmset", "-g", "batt"]:
            return fake_runner(command, timeout, interactive)
        persisted = state.load_state()
        assert persisted is not None
        assert list(command) in persisted["pending_commands"]
        observed.append(tuple(command))
        return CmdResult("", "", 0)

    set_runner(runner)
    actions = [EnableSleep(), SetPowerProfile(["sleep", "5"]), WifiOff("en0"), BluetoothOff()]
    assert Executor(Context(snapshot)).execute_all(actions) == [("", 0)] * 4
    assert set(observed) == {
        ("sudo", "pmset", "-a", "disablesleep", "0"),
        ("sudo", "pmset", "-b", "sleep", "5"),
        ("sudo", "networksetup", "-setairportpower", "en0", "off"),
        ("blueutil", "-p", "0"),
    }
    restored = state.load_state()
    assert restored["batt_prof"] == "sleep 1"
    assert restored["wifi_off"] == "en0"
    assert restored["bt_off"] == "1"
    assert restored["pending_commands"] == []
    assert all(action.describe() and action.risk in {"safe", "aggressive"} for action in actions)


def test_action_disk_failure_prevents_mutation(fake_runner):
    snapshot, _ = snapshots.snapshot_origin()
    before = list(fake_runner.calls)

    def fail_save(_state):
        raise state.StateError("fixture disk full")

    with pytest.raises(state.StateError, match="fixture disk full"):
        Executor(Context(snapshot, fail_save)).execute(WifiOff("en0"))
    assert fake_runner.calls == before

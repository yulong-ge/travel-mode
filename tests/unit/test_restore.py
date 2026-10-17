import travel_mode.flows.restore as flow_restore
from travel_mode.system import pmset
from travel_mode.system.shell import CmdResult


def test_run_restore_emits_complete_power_restore_sequence(monkeypatch, fake_runner):
    state = {
        "batt_prof": "sleep 5 displaysleep 3",
        "ac_prof": "powernap 0",
        "sleepdisabled": "0",
        "unloaded_sys_svcs": [],
        "unloaded_svcs": [],
        "frozen_apps": [],
        "quit_apps": [],
    }
    monkeypatch.setattr(pmset, "_profile_args", lambda value: value.split())
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)

    count, failures = flow_restore.run_restore(state, lambda *_: None)

    assert failures == 0
    assert count == 3
    assert [command for command in fake_runner.calls if command[:2] == ("sudo", "pmset")] == [
        ("sudo", "pmset", "-b", "sleep", "5", "displaysleep", "3"),
        ("sudo", "pmset", "-c", "powernap", "0"),
        ("sudo", "pmset", "-a", "disablesleep", "0"),
    ]

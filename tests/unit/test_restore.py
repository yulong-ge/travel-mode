import travel_mode.flows.restore as flow_restore
from travel_mode.system import pmset


def test_run_restore_emits_complete_power_restore_sequence(monkeypatch):
    state = {
        "batt_prof": "sleep 5 displaysleep 3",
        "ac_prof": "powernap 0",
        "sleepdisabled": "0",
        "unloaded_sys_svcs": [],
        "unloaded_svcs": [],
        "frozen_apps": [],
        "quit_apps": [],
    }
    calls = []
    monkeypatch.setattr(pmset, "_profile_args", lambda value: value.split())
    monkeypatch.setattr(
        flow_restore,
        "run_rc",
        lambda command, **kwargs: calls.append((command, kwargs.get("sudo", False))) or ("", 0),
    )

    count, failures = flow_restore.run_restore(state, lambda *_: None)

    assert failures == 0
    assert count == 3
    assert calls == [
        (["pmset", "-b", "sleep", "5", "displaysleep", "3"], True),
        (["pmset", "-c", "powernap", "0"], True),
        (["pmset", "-a", "disablesleep", "0"], True),
    ]

import travel_mode.flows.check as flow_check
from travel_mode.system import pmset, processes
from travel_mode.system.pmset import sleep_blocking_assertions


def test_sleep_assertion_process_and_daemon_match():
    lines = [
        ' pid 42(Remote Support): PreventUserIdleSystemSleep named: "keep awake"',
        ' pid 43(Other App): PreventSystemSleep named: "rendering"',
        ' pid 44(helper): PreventUserIdleSystemSleep named: "Remote Support helper"',
    ]
    daemon = type("Spec", (), {"name": "Remote Support"})()
    rows = sleep_blocking_assertions(lines, [daemon])
    assert rows[0]["pid"] == "42"
    assert rows[0]["daemon_app"] is True
    assert rows[1]["type"] == "PreventSystemSleep"
    assert rows[1]["daemon_app"] is False
    assert rows[2]["daemon_app"] is True


def test_blockers_lines_explains_daemon_match_and_lists_all_holders(monkeypatch):
    monkeypatch.setattr(pmset, "sleep_disabled", lambda: "0")
    monkeypatch.setattr(processes, "caffeinate_procs", lambda: [])
    monkeypatch.setattr(
        pmset,
        "configured_sleep_blocking_assertions",
        lambda: sleep_blocking_assertions(
            [
                'pid 42(Remote Support): PreventSystemSleep named: "Remote Support"',
                'pid 43(Other): PreventUserIdleSystemSleep named: "other"',
            ],
            [type("Spec", (), {"name": "Remote Support"})()],
        ),
    )
    monkeypatch.setattr(pmset, "audio_assertion", lambda: "")

    lines = flow_check.blockers_lines()
    messages = [message for _, message in lines]
    assert any("Remote Support" in message and "系统睡眠" in message for message in messages)
    assert any("Other" in message for message in messages)

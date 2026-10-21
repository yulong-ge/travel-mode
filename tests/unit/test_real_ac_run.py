"""Real-machine regressions, modeled with isolated observations and commands."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Event

import pytest

from session_interval_support import interval_case
from travel_mode import live_power, state
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.actions.power import SetPowerProfile
from travel_mode.i18n import set_language, t
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult, ReadError


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_configured_quit_uses_quit_reason_instead_of_freeze_reason(scenario, monkeypatch):
    from travel_mode.system import apps
    from travel_mode.tui.plan import collect
    from travel_mode.tui.view_models import plan_view

    monkeypatch.setattr(
        apps,
        "scan_apps",
        lambda: dict(
            running=[dict(name="Quit fixture", path="/Fixture/Quit.app", defact="quit", reason="freeze")],
            nobundle=[],
        ),
    )
    row = next(row for row in collect(scenario, {})["rows"] if row["descriptor"]["kind"] == "app")
    row.update(state="done", result=t("app_view.exited"))
    assert plan_view(row)["reason"] == t("redesign.quit_reason")
    row["action"] = "freeze"
    assert plan_view(row)["reason"] == t("setup.reason_freeze")
    row["reason_key"] = "setup.reason_freeze"
    row["action"] = "quit"
    assert plan_view(row)["reason"] == t("redesign.quit_reason")


@pytest.mark.parametrize(
    "language,expected", [("en", "Stop Spotlight indexing"), ("zh", "停止 Spotlight 索引")]
)
def test_spotlight_action_label_leaves_password_explanation_to_dialog(language, expected):
    set_language(language)
    assert t("health.action.spotlight") == expected


def test_live_sleep_detection_uses_only_kernel_times_and_rejects_pre_session_sleep(fake_runner):
    start, _, reading = interval_case("normal")
    active = dict(id="kernel-fixture", start=start, started_at=start["at"], external_seen=False)
    now = datetime.fromisoformat(start["at"]) + timedelta(hours=1)
    command = ("sysctl", "-n", "kern.sleeptime", "kern.waketime")
    slept = int(now.timestamp()) - 60
    fake_runner.responses[command] = CmdResult(
        f"{{ sec = {slept}, usec = 12 }} date\n{{ sec = {slept + 30}, usec = 500000 }} date\n", "", 0
    )
    observed = live_power.observe(active, reading, now)
    assert observed["estimate_source"] == "session_including_sleep"
    assert observed["remaining_minutes"] is None
    assert fake_runner.calls == [command]
    previous = int(datetime.fromisoformat(start["at"]).timestamp()) - 30
    fake_runner.responses[command] = CmdResult(
        f"{{ sec = {previous}, usec = 0 }}\n{{ sec = {previous + 10}, usec = 0 }}\n", "", 0
    )
    observed = live_power.observe(active, reading, now)
    assert observed["estimate_source"] == "session_telemetry"
    assert observed["remaining_minutes"] is not None
    assert all(call == command for call in fake_runner.calls)


@pytest.mark.parametrize("text", ["", "{ sec = 1, usec = 1000000 }\n{ sec = 2, usec = 0 }", "broken\nbroken"])
def test_invalid_kernel_sleep_times_fail_explicitly(text):
    with pytest.raises(ReadError, match="sysctl"):
        live_power.sleep_events(text)


def test_pmset_domains_share_one_serial_writer_and_preserve_both_changes(fake_runner):
    first_started, release = Event(), Event()
    writes = []
    preferences = dict(b="0", c="1")
    saves = []
    snapshot = state.new_state()
    snapshot["power_origin"] = dict(batt_prof="lowpowermode 0", ac_prof="powernap 1")

    def runner(command, timeout=None, interactive=False):
        if command[:2] == ["sudo", "pmset"]:
            # Model pmset's shared preferences read/modify/write operation.
            copied = deepcopy(preferences)
            domain, value = command[2][1:], command[4]
            writes.append(domain)
            if domain == "b":
                first_started.set()
                assert release.wait(10)
            copied[domain] = value
            preferences.update(copied)
            return CmdResult("", "", 0)
        return fake_runner(command, timeout, interactive)

    shell.set_runner(runner)
    actions = [SetPowerProfile(["lowpowermode", "1"], "-b"), SetPowerProfile(["powernap", "0"], "-c")]

    def execute():
        return Executor(Context(snapshot, lambda value: saves.append(deepcopy(value)))).execute_all(actions)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute)
        try:
            assert first_started.wait(10)
            assert writes == ["b"]
        finally:
            release.set()
        assert all(result[1] == 0 for result in future.result(timeout=10))
    assert writes == ["b", "c"] and preferences == dict(b="1", c="0")
    assert snapshot["session"]["actions"] == dict(attempted=2, succeeded=2, failed=0)
    assert saves

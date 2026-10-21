"""Concurrent recovery boundaries and passive telemetry contracts."""

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, get_ident, local

import pytest

from session_interval_support import interval_case
from travel_mode import failures, live_power, measurement, state, status
from travel_mode.actions.apps import FreezeApp, QuitApp
from travel_mode.actions.base import Context, RecoveryCommand
from travel_mode.actions.executor import Executor
from travel_mode.i18n import t
from travel_mode.system import apps, shell
from travel_mode.system.shell import CmdResult


class LogicalClock:
    """Each worker advances a controllable timeline; elapsed is the longest lane."""

    def __init__(self):
        self.thread = local()
        self.elapsed = 0

    def monotonic(self):
        return getattr(self.thread, "now", 0)

    def sleep(self, seconds):
        self.thread.now = self.monotonic() + seconds
        self.elapsed = max(self.elapsed, self.thread.now)


@pytest.mark.parametrize("order", [(2, 0, 1), (1, 2, 0)])
def test_batch_out_of_order_completion_has_single_writer_and_durable_intents(monkeypatch, fake_runner, order):
    snapshot = state.new_state()
    writer = get_ident()
    commands, saves, completed = [], [], []
    ready = Barrier(3)
    gates = [Event() for _ in range(3)]
    gates[order[0]].set()
    names = [f"/Fixture/App{i}.app" for i in range(3)]
    monkeypatch.setattr(apps, "app_bid", lambda p: "")
    monkeypatch.setattr(apps, "bundle_alive", lambda p: False)
    monkeypatch.setattr(
        apps,
        "app_running",
        lambda p: (
            not gates[names.index(p)].is_set()
            if any(p.split("/")[-1][:-4] in c[4] for c in commands)
            else True
        ),
    )

    def save(value):
        assert get_ident() == writer
        saves.append(deepcopy(value))
        state.save_state(value)

    def runner(command, timeout=None, interactive=False):
        if command[0] != "osascript":
            return fake_runner(command, timeout, interactive)
        commands.append(command)
        path = next(path for path in names if path.split("/")[-1][:-4] in command[4])
        assert path in state.load_state()["quit_apps"]
        ready.wait(timeout=5)
        gates[names.index(path)].wait(timeout=5)
        assert gates[names.index(path)].is_set()
        return CmdResult("", "", 0)

    def complete(index, result):
        assert get_ident() == writer and result
        completed.append(index)
        if len(completed) < 3:
            gates[order[len(completed)]].set()

    shell.set_runner(runner)
    results = Executor(Context(snapshot, save)).execute_all(
        [QuitApp(path, f"App{i}") for i, path in enumerate(names)], completed=complete
    )
    assert completed == list(order) and all(results)
    assert set(snapshot["quit_apps"]) == set(names)
    assert snapshot["session"]["actions"] == dict(attempted=3, succeeded=3, failed=0)
    assert state.load_state()["quit_apps"] == snapshot["quit_apps"]
    assert saves


@pytest.mark.parametrize("refused", [(), (0,), (0, 1)])
@pytest.mark.parametrize("request_seconds", [0, 8])
def test_shared_quit_deadline_mixed_refusal_and_success(monkeypatch, fake_runner, refused, request_seconds):
    clock = LogicalClock()
    ready = Barrier(2)
    requested = set()
    names = ["/Fixture/A.app", "/Fixture/B.app"]
    monkeypatch.setattr(apps, "app_bid", lambda p: "")
    monkeypatch.setattr(apps, "app_running", lambda p: p not in requested or names.index(p) in refused)
    monkeypatch.setattr(apps, "bundle_alive", lambda p: names.index(p) in refused)

    def runner(command, timeout=None, interactive=False):
        if command[0] == "osascript":
            index = 0 if '"A"' in command[4] else 1
            requested.add(names[index])
            clock.sleep(request_seconds)
            ready.wait(timeout=5)
            return CmdResult("", "User canceled. (-128)" if index in refused else "", int(index in refused))
        return fake_runner(command, timeout, interactive)

    shell.set_runner(runner)
    snapshot = state.new_state()
    results = Executor(Context(snapshot)).execute_all([QuitApp(p, p[-5]) for p in names], clock=clock)
    assert [bool(r) for r in results] == [i not in refused for i in range(2)]
    assert clock.elapsed == request_seconds + (10 if refused else 0)
    assert snapshot["session"]["actions"]["failed"] == len(refused)
    assert all(r["quit_status"] == "refused" for r in snapshot.get("action_failures", []))


def test_recovered_retry_uses_exact_identity_for_long_path(monkeypatch, fake_runner):
    path = "/Fixture/" + "long-directory/" * 25 + "Sync.app"
    snapshot = state.new_state()
    results = iter([False, True])
    monkeypatch.setattr(apps, "freeze_app", lambda p: next(results))
    executor = Executor(Context(snapshot))
    assert not executor.execute(FreezeApp(path))
    assert len(snapshot["action_failures"][0]["action"]) == 200
    assert executor.execute(FreezeApp(path))
    receipt = state.load_state()["action_failures"][0]
    assert receipt["identity"] == dict(type="FreezeApp", target=path)
    assert receipt["recovered"] is True
    assert failures.session_receipts(snapshot)[0]["recovered"] is True


@pytest.mark.parametrize(
    "before,after,count,expected",
    [
        (8.8, 8.5, 1, "执行前 8.8 W → 执行后 8.5 W，差异在测量误差范围内（单次数据，精度较低）。"),
        (23.1, 13.0, 2, "执行前 23.1 W → 执行后 13.0 W，约节省 10.1 W。"),
        (20, 18.1, 2, "执行前 20.0 W → 执行后 18.1 W，差异在测量误差范围内。"),
    ],
)
def test_passive_measurement_display_threshold(before, after, count, expected):
    comparison = dict(
        mode="passive",
        before=dict(available=True, average_watts=before, interval_count=count),
        after=dict(available=True, average_watts=after, interval_count=count),
    )
    assert measurement.comparison_description(comparison) == expected
    comparison["after"] = None
    assert measurement.comparison_description(comparison) == "执行后功耗：正在等待新的电池数据。"


def test_sleep_estimate_waits_for_fresh_post_wake_interval_and_is_shared():
    start, _initial, reading = interval_case("normal")
    active = dict(id="fixture", start=start, started_at=start["at"], external_seen=False)
    now = datetime.fromisoformat(start["at"]) + timedelta(hours=9)
    sleep = (now - timedelta(hours=8)).timestamp()
    wake = (now - timedelta(seconds=5)).timestamp()
    events = live_power.sleep_events(f"{{ sec = {int(sleep)}, usec = 0 }}\n{{ sec = {int(wake)}, usec = 0 }}")
    cache = {}
    pending = live_power.estimate(active, reading, now, events, cache)
    assert pending["remaining_minutes"] is None
    assert pending["estimate_source"] == "session_including_sleep"
    assert pending["average_watts"] is not None
    fresh = deepcopy(reading)
    fresh["telemetry"]["system_count"] += 10
    fresh["telemetry"]["system_sum_mw"] += 98000
    fresh["remaining_wh"] = 9.8 * (3 + 40 / 60)
    baseline = live_power.estimate(active, fresh, now + timedelta(seconds=30), events, cache)
    assert baseline["remaining_minutes"] is None
    fresh = deepcopy(fresh)
    fresh["telemetry"]["system_count"] += 10
    fresh["telemetry"]["system_sum_mw"] += 98000
    result = live_power.estimate(active, fresh, now + timedelta(seconds=60), events, cache)
    assert result["average_watts"] == 9.8 and result["remaining_minutes"] == 220
    assert result["estimate_source"] == "wake_telemetry"
    result.update(percent=65)
    line = status.power_line(dict(power_source="battery", battery=result))
    assert line == "电池 65% · 按唤醒后平均 9.8 W 约可用 3 小时 40 分"
    from travel_mode.tui.session_view import session_view

    view = session_view(dict(session=active), fresh, now=now + timedelta(seconds=30), estimate=result)
    assert t("menubar.wake_remaining", watts=9.8, duration=status.duration(220)) in view["lines"]
    # A later wake invalidates the earlier active-use estimate.
    later_wake = (now + timedelta(seconds=70)).timestamp()
    next_result = live_power.estimate(active, fresh, now + timedelta(seconds=80), (sleep, later_wake), cache)
    assert next_result["remaining_minutes"] is None


def test_restore_dispatches_app_requests_in_recorded_order_without_waiting(tmp_path, fake_runner):
    from travel_mode.flows.restore import run_restore

    paths = [str(tmp_path / f"App{i}.app") for i in range(3)]
    for path in paths:
        Path(path).mkdir()
    snapshot = state.new_state()
    snapshot["quit_apps"] = list(paths)
    state.save_state(snapshot)
    ready = Barrier(3)
    issued = []
    writers = []

    def runner(command, timeout=None, interactive=False):
        if command[0] == "open":
            issued.append(command[1])
            shell.command_started()
            ready.wait(timeout=5)
            return CmdResult("", "", 0)
        return fake_runner(command, timeout, interactive)

    shell.set_runner(runner)
    assert run_restore(snapshot, lambda *args: writers.append(get_ident())) == (3, 0)
    assert issued == paths
    assert set(writers) == {get_ident()}
    assert state.load_state()["quit_apps"] == []


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_batch_failure_output_and_summary_follow_plan_order(order, fake_runner):
    snapshot = state.new_state()
    ready = Barrier(2)
    gates = [Event(), Event()]
    gates[order[0]].set()
    commands = [["fixture", str(i)] for i in range(2)]
    actions = [RecoveryCommand(command, f"item-{i}") for i, command in enumerate(commands)]
    output = []

    def runner(command, timeout=None, interactive=False):
        if command[:1] != ["fixture"]:
            return fake_runner(command, timeout, interactive)
        i = int(command[1])
        ready.wait(timeout=5)
        assert gates[i].wait(5)
        if i == order[0]:
            gates[order[1]].set()
        return CmdResult(f"failure-{i}", "", 1)

    shell.set_runner(runner)
    with failures.reporter(lambda kind, text: output.append(text)), failures.batch() as counts:
        results = Executor(Context(snapshot)).execute_all(actions)
        assert [result[0] for result in results] == ["failure-0", "failure-1"]
        assert counts["failed"] == [failures.action_name(action) for action in actions]
    assert output[0].find("failure-0") >= 0
    assert output[1].find("failure-1") >= 0


def test_interrupted_batch_drains_workers_before_propagating(monkeypatch, fake_runner):
    from queue import Queue

    from travel_mode.actions import batch

    class InterruptOnce(Queue):
        interrupted = False

        def get(self, *args, **kwargs):
            if not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt("fixture cancellation")
            return super().get(*args, **kwargs)

    monkeypatch.setattr(batch, "Queue", InterruptOnce)
    snapshot = state.new_state()
    actions = [QuitApp(f"/Fixture/App{i}.app", f"App{i}") for i in range(3)]
    with pytest.raises(KeyboardInterrupt, match="fixture cancellation"):
        Executor(Context(snapshot)).execute_all(actions)
    assert not any(call[0] == "osascript" for call in fake_runner.calls)


def test_parallel_restore_success_does_not_resolve_another_failed_service(tmp_path, fake_runner, monkeypatch):
    import plistlib

    from travel_mode.flows.restore import run_restore

    paths = [str(tmp_path / f"service-{i}.plist") for i in range(2)]
    for i, path in enumerate(paths):
        Path(path).write_bytes(plistlib.dumps(dict(Label=f"org.demo.service{i}")))
    snapshot = state.new_state()
    snapshot["unloaded_svcs"] = list(paths)
    state.save_state(snapshot)
    failure_saved = Event()
    original_save = state.save_state

    def save(value):
        original_save(value)
        if value.get("action_failures"):
            failure_saved.set()

    def runner(command, timeout=None, interactive=False):
        if command == ["launchctl", "load", paths[0]]:
            return CmdResult("", "fixture load denied", 1)
        if command == ["launchctl", "load", paths[1]]:
            assert failure_saved.wait(5)
            return CmdResult("", "", 0)
        return fake_runner(command, timeout, interactive)

    monkeypatch.setattr(state, "save_state", save)
    shell.set_runner(runner)
    assert run_restore(snapshot, lambda *args: None) == (1, 1)
    saved = state.load_state()
    assert saved["unloaded_svcs"] == [paths[0]]
    assert not saved["action_failures"][0].get("recovered", False)
    shell.set_runner(fake_runner)
    fake_runner.responses[("launchctl", "load", paths[0])] = CmdResult("", "", 0)
    assert run_restore(saved, lambda *args: None) == (1, 0)
    assert state.load_state()["action_failures"][0]["recovered"] is True


def test_batch_main_exit_completes_without_waiting_for_helpers(monkeypatch, fake_runner):
    requested = set()
    clock = LogicalClock()
    monkeypatch.setattr(apps, "app_bid", lambda path: "")
    monkeypatch.setattr(apps, "app_running", lambda path: Path(path).stem not in requested)
    monkeypatch.setattr(apps, "bundle_alive", lambda path: True)

    def runner(command, timeout=None, interactive=False):
        if command[0] == "osascript":
            requested.add(command[4].split('"')[1])
            return CmdResult("", "", 0)
        return fake_runner(command, timeout, interactive)

    shell.set_runner(runner)
    results = Executor(Context(state.new_state())).execute_all(
        [QuitApp(f"/Fixture/App{i}.app", f"App{i}") for i in range(2)], clock=clock
    )
    assert all(result and result.helpers_remaining for result in results)
    assert clock.elapsed == 0


def test_retry_of_another_pid_cannot_resolve_a_failed_process_action(monkeypatch):
    from travel_mode.actions.power import KillCaffeinate
    from travel_mode.system import processes
    from travel_mode.system.process_control import Process

    snapshot = state.new_state()
    attempts = iter([("denied", 1), ("", 0), ("", 0)])
    monkeypatch.setattr(processes, "terminate_caffeinate", lambda target: next(attempts))
    actions = [
        KillCaffeinate(
            processes.CaffeinateTarget(
                Process(pid, 1, "S", "Thu Oct 15 12:00:00 2026", "/usr/bin/caffeinate"),
                "/usr/bin/caffeinate -i",
                "fixture",
            )
        )
        for pid in (101, 102)
    ]
    executor = Executor(Context(snapshot))
    assert executor.execute(actions[0])[1] == 1
    assert executor.execute(actions[1])[1] == 0
    assert not snapshot["action_failures"][0].get("recovered", False)
    assert executor.execute(actions[0])[1] == 0
    assert state.load_state()["action_failures"][0]["recovered"] is True


def test_passive_source_change_invalidates_savings_without_corrupting_state():
    from pathlib import Path

    from travel_mode.system import battery

    reading = battery.parse((Path(__file__).parents[1] / "fixtures/battery/discharging.txt").read_text())
    clock = datetime.fromisoformat("2026-10-15T12:00:00+00:00")
    passive = measurement.PassiveComparison()
    passive.observe(dict(reading, update_time=1000, watts=12), clock)
    passive.observe(dict(reading, update_time=1001, watts=12), clock + timedelta(seconds=30))
    result = passive.begin()
    passive.complete(clock + timedelta(seconds=31))
    for i in range(3):
        passive.observe(dict(reading, update_time=1002 + i, watts=6), clock + timedelta(seconds=60 + i * 30))
    assert result["saved_watts"] == 6
    changed = dict(
        reading,
        update_time=1005,
        power_source="PowerTelemetryData.SystemLoad",
        telemetry=dict(system_count=100, system_sum_mw=1_000_000),
    )
    with pytest.raises(battery.IntervalError) as error:
        passive.observe(changed, clock + timedelta(seconds=150))
    assert error.value.reason == "source_changed"
    passive.observe(
        dict(changed, update_time=1006, telemetry=dict(system_count=101, system_sum_mw=1_005_000)),
        clock + timedelta(seconds=180),
    )
    assert result["before"]["reason"] == "source_changed"
    assert result["saved_watts"] is None and result["extra_hours"] is None
    snapshot = state.new_state()
    snapshot["measurement"] = result
    state.save_state(snapshot)
    assert state.load_state()["measurement"] == result

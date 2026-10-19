"""Event-controlled concurrency and error regressions use the isolated FakeRunner."""

import asyncio
import json
import plistlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from textual.message import Message
from textual.widgets import Button

import travel_mode.flows.apply as flow_apply
import travel_mode.flows.restore as flow_restore
import travel_mode.network_apps as network_api
from travel_mode import state
from travel_mode.i18n import get_language, set_language, t
from travel_mode.paths import config_path, state_path
from travel_mode.system import apps as system_apps
from travel_mode.system import launchd, shell
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp


@pytest.fixture(autouse=True)
def english_messages():
    previous = get_language()
    set_language("en")
    try:
        yield
    finally:
        set_language(previous)


@pytest.fixture
def action_ui(monkeypatch, tmp_paths, fake_runner):
    config_path().write_text("")
    rows = [
        {"name": name, "path": str(tmp_paths / f"{name}.app"), "cpu": "0", "defact": "freeze"}
        for name in ("DemoA", "DemoB")
    ]
    monkeypatch.setattr(system_apps, "APP_DIRS", [])
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": list(rows), "nobundle": []})
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": [], "kept": []})
    fake_runner.responses[("pmset", "-g", "assertions")] = CmdResult(
        'Assertion status system-wide:\npid 777(caffeinate): PreventUserIdleSystemSleep named: "test"\n',
        "",
        0,
    )
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        "777 1 S Thu Oct 15 12:00:00 2026 /usr/bin/caffeinate\n", "", 0
    )
    fake_runner.responses[("ps", "-ww", "-axo", "pid=,command=")] = CmdResult(
        "777 /usr/bin/caffeinate -i\n", "", 0
    )
    import os

    from travel_mode.system.health import PROCESS_COMMAND

    fake_runner.responses[tuple(PROCESS_COMMAND)] = CmdResult(
        f"777 1 {os.getuid()} S 0 Thu Oct 15 12:00:00 2026 /usr/bin/caffeinate", "", 0
    )
    fake_runner.responses[("ps", "-ww", "-p", "777", "-o", "command=")] = CmdResult(
        "/usr/bin/caffeinate -i", "", 0
    )
    monkeypatch.setattr(system_apps, "load_conf", lambda: ({}, "freeze"))
    monkeypatch.setattr(system_apps, "app_sleep_assertions", lambda name: [])
    return rows


class BlockingRunner:
    def __init__(self, fake, command):
        self.fake, self.command = fake, command
        self.entered, self.release = threading.Event(), threading.Event()

    def __call__(self, cmd, timeout=None, interactive=False):
        if tuple(cmd) == self.command:
            self.entered.set()
            if not self.release.wait(10):
                raise TimeoutError(f"test did not release {self.command}")
        return self.fake(cmd, timeout, interactive)


async def entered(blocker):
    assert await asyncio.to_thread(blocker.entered.wait, 5), "command was not reached"


async def settled(app, pilot):
    await pilot.pause()
    await app.wait_for_idle()
    await pilot.pause()


class ButtonQueueProbe(Message):
    """A marker behind the second press in the button's message queue."""


async def queued(button, probe_seen):
    button.post_message(ButtonQueueProbe())
    await asyncio.wait_for(probe_seen.wait(), timeout=5)


async def test_tui_batch_then_off_button_events_are_serial(action_ui, fake_runner, monkeypatch):
    from tui_plan import select_plan

    command = ("test-freeze", action_ui[0]["path"])
    fake_runner.responses[("test-freeze",)] = CmdResult("", "", 0)
    blocker = BlockingRunner(fake_runner, command)
    shell.set_runner(blocker)
    order = []

    def freeze(path):
        shell.run_rc(["test-freeze", path])
        order.append("freeze")
        return True

    def restore(st, emit):
        order.append("off")
        return 2, 0

    monkeypatch.setattr(system_apps, "freeze_app", freeze)
    monkeypatch.setattr(flow_restore, "run_restore", restore)
    app = TravelApp(scenario="away", measure=False)
    probe_seen = asyncio.Event()
    async with app.run_test(
        message_hook=lambda msg: probe_seen.set() if isinstance(msg, ButtonQueueProbe) else None
    ) as pilot:
        await settled(app, pilot)
        await select_plan(app, group="apps")
        app.query_one("#b-quick", Button).press()
        try:
            await entered(blocker)
            app.query_one("#b-off", Button).press()
            await queued(app.query_one("#b-off", Button), probe_seen)
            assert "off" not in order
        finally:
            blocker.release.set()
        await settled(app, pilot)
        assert order == ["freeze", "freeze"]


@pytest.mark.parametrize("action", ["f", "q"])
async def test_tui_off_then_single_button_events_are_serial(action_ui, fake_runner, monkeypatch, action):
    snapshot = state.new_state()
    snapshot["quit_apps"] = [action_ui[0]["path"]]
    state.save_state(snapshot)
    command = ("open", "-a", action_ui[0]["path"])
    fake_runner.responses[("open",)] = CmdResult("", "", 0)
    blocker = BlockingRunner(fake_runner, command)
    shell.set_runner(blocker)
    order = []

    def restore(st, emit):
        shell.run_rc(list(command))
        order.append("off")
        return 1, 0

    monkeypatch.setattr(flow_restore, "run_restore", restore)
    monkeypatch.setattr(system_apps, "freeze_app", lambda *a: order.append("single") or True)
    monkeypatch.setattr(system_apps, "grace_quit", lambda *a: order.append("single") or True)
    app = TravelApp(scenario="away", measure=False)
    probe_seen = asyncio.Event()
    async with app.run_test(
        message_hook=lambda msg: probe_seen.set() if isinstance(msg, ButtonQueueProbe) else None
    ) as pilot:
        await settled(app, pilot)
        next(r for r in app.plan_items if r["name"] == "DemoB")["action"] = (
            "freeze" if action == "f" else "quit"
        )
        app.query_one("#b-off", Button).press()
        try:
            await entered(blocker)
            app.query_one("#b-quick", Button).press()
            await queued(app.query_one("#b-quick", Button), probe_seen)
            assert not order
        finally:
            blocker.release.set()
        await settled(app, pilot)
        assert order == ["off"]


def test_concurrent_save_state_unique_temporary_files(monkeypatch, tmp_paths):
    barrier = threading.Barrier(2)
    original_path_replace, original_replace = Path.replace, state.os.replace
    sources = []

    def replace(source, target):
        sources.append(str(source))
        barrier.wait(timeout=5)
        return original_replace(source, target)

    def path_replace(source, target):
        sources.append(str(source))
        barrier.wait(timeout=5)
        return original_path_replace(source, target)

    monkeypatch.setattr(Path, "replace", path_replace)
    monkeypatch.setattr(state.os, "replace", replace)
    snapshots = [state.new_state(), state.new_state()]
    for index, snapshot in enumerate(snapshots):
        snapshot["quit_apps"] = [f"/Demo{index}.app"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(state.save_state, snapshot) for snapshot in snapshots]
        errors = [future.exception() for future in futures]
    assert errors == [None, None], repr(errors)
    assert len(set(sources)) == 2
    assert json.loads(state_path().read_text())["quit_apps"] in [["/Demo0.app"], ["/Demo1.app"]]
    assert list(tmp_paths.glob("*.tmp")) == []


async def test_tui_refresh_recompose_batch_uses_original_rows(action_ui, fake_runner, monkeypatch):
    from tui_plan import select_plan

    command = ("test-freeze", action_ui[0]["path"])
    fake_runner.responses[("test-freeze",)] = CmdResult("", "", 0)
    blocker = BlockingRunner(fake_runner, command)
    shell.set_runner(blocker)
    frozen = []

    def freeze(path):
        shell.run_rc(["test-freeze", path])
        frozen.append(path)
        return True

    monkeypatch.setattr(system_apps, "freeze_app", freeze)
    app = TravelApp(scenario="away", measure=False)
    originals = [r["path"] for r in action_ui]
    async with app.run_test() as pilot:
        await settled(app, pilot)
        await select_plan(app, group="apps")
        task = asyncio.create_task(app.on_button_pressed(Button.Pressed(app.query_one("#b-quick"))))
        switch = None
        try:
            await entered(blocker)
            action_ui[:] = [dict(action_ui[1], name="Replacement", path="/Replacement.app")]
            app.action_refresh()
            switch = asyncio.create_task(app.action_toggle_language())
            assert app._refresh_after_action and not switch.done()
        finally:
            blocker.release.set()
            await task
            if switch is not None:
                await switch
        await settled(app, pilot)
        assert frozen == originals and app.st["frozen_apps"] == originals
        app.view_mode = "plan"
        app.plan_items = []
        app.action_refresh()
        await app.wait_for_idle()
        replacement = next(r for r in app.plan_items if r["name"] == "Replacement")
        assert replacement["descriptor"]["path"] == "/Replacement.app" and replacement["state"] == "planned"


async def test_tui_double_click_admits_one_mutation(action_ui, fake_runner):
    command = ("kill", "-TERM", "777")
    fake_runner.responses[command] = CmdResult("", "", 0)
    blocker = BlockingRunner(fake_runner, command)
    shell.set_runner(blocker)
    app = TravelApp(scenario="away")
    async with app.run_test() as pilot:
        await settled(app, pilot)
        from tui_interaction import click_after_layout

        await click_after_layout(pilot, "#b-suggestions")
        click = asyncio.create_task(pilot.click("#b-killcaff-777", times=2))
        try:
            await entered(blocker)
            # Both physical clicks are posted by Pilot before it waits for idle.
        finally:
            blocker.release.set()
        assert await click
        await settled(app, pilot)
        assert fake_runner.calls.count(command) == 1


async def test_tui_queued_duplicate_button_messages_are_rejected(action_ui, fake_runner):
    command = ("kill", "-TERM", "777")
    fake_runner.responses[command] = CmdResult("", "", 0)
    app = TravelApp(scenario="away")
    async with app.run_test() as pilot:
        await settled(app, pilot)
        from tui_interaction import click_after_layout

        await click_after_layout(pilot, "#b-suggestions")
        button = app.query_one("#b-killcaff-777", Button)
        button.press().press()
        await settled(app, pilot)
        assert fake_runner.calls.count(command) == 1


def test_time_machine_unparseable_status_is_explicit_unknown(monkeypatch, fake_runner):
    monkeypatch.setattr(launchd, "command_path", lambda name: "/usr/bin/tmutil")
    fake_runner.responses[("tmutil", "status")] = CmdResult("Unexpected status output", "", 0)
    info = launchd.time_machine_info()
    lines = launchd.service_information({"time_machine": info})
    assert info["running"] is None
    assert "tmutil status" in info["error"]
    assert t("services.tm_idle") not in "\n".join(lines)
    assert info["error"] in "\n".join(lines)


def test_service_cpu_query_failure_is_explicit(tmp_paths, fake_runner):
    plist = tmp_paths / "demo.plist"
    plist.write_bytes(plistlib.dumps({"Label": "com.example.demo"}))
    fake_runner.responses[("launchctl", "list")] = CmdResult("777 0 com.example.demo\n", "", 0)
    fake_runner.responses[("ps", "-p", "777")] = CmdResult("", "denied", 2)
    scan = launchd.svc_scan(directories=(tmp_paths,))
    assert scan["recognized"][0]["cpu"] == ""
    assert "denied" in scan["recognized"][0]["cpu_error"]
    assert any("denied" in message for message in launchd.service_information(scan))
    from travel_mode.failures import log_path

    assert "denied" in log_path().read_text()


async def test_tui_caffeinate_failure_never_logs_success(action_ui, fake_runner):
    command = ("kill", "-TERM", "777")
    fake_runner.responses[command] = CmdResult("", "denied", 2)
    app = TravelApp(scenario="away")
    async with app.run_test() as pilot:
        await settled(app, pilot)
        from tui_interaction import click_after_layout

        await click_after_layout(pilot, "#b-suggestions")
        messages = []
        app.logline = lambda *args: messages.append(args)
        await app._dispatch_button(Button.Pressed(app.query_one("#b-killcaff-777")))
        assert app.is_running and app._exception is None
        assert any("denied" in text and "travel-mode.log" in text for _, text in messages)
        assert not any(kind == "ok" for kind, _ in messages)


def test_display_sleep_failure_is_explicit(monkeypatch, fake_runner, capsys):
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": [], "kept": []})
    monkeypatch.setattr(network_api, "configured_names", lambda: [])
    monkeypatch.setattr(flow_apply, "ask", lambda message, **kw: "displaysleepnow" in message)
    fake_runner.responses[("pmset", "displaysleepnow")] = CmdResult("", "denied", 3)
    assert flow_apply.mode_apply(yes=True) == 1
    captured = capsys.readouterr()
    assert "denied" in captured.out + captured.err and "Traceback" not in captured.out + captured.err


@pytest.mark.parametrize("first", ["b-quick", "b-off"])
async def test_tui_transaction_disables_mutations_across_recompose(
    first, action_ui, fake_runner, monkeypatch
):
    from tui_plan import select_plan

    command = ("test-transaction",)
    fake_runner.responses[command] = CmdResult("", "", 0)
    blocker = BlockingRunner(fake_runner, command)
    shell.set_runner(blocker)
    snapshot = state.new_state()
    snapshot["quit_apps"] = [action_ui[0]["path"]]
    state.save_state(snapshot)
    monkeypatch.setattr(system_apps, "freeze_app", lambda path: shell.run_rc(list(command))[1] == 0)
    monkeypatch.setattr(flow_restore, "run_restore", lambda st, emit: (shell.run_rc(list(command))[1] + 1, 0))
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test() as pilot:
        await settled(app, pilot)
        app._session_admitted = True
        if first == "b-quick":
            await select_plan(app, group="apps")
        task = asyncio.create_task(app.on_button_pressed(Button.Pressed(app.query_one("#" + first))))
        switch = None
        try:
            await entered(blocker)
            assert app._mutation_lock.locked()
            old_health = app.query_one("#dash-health")
            assert app.query_one("#b-off", Button).disabled and app.query_one("#b-quick", Button).disabled
            await app.on_button_pressed(Button.Pressed(Button(id="b-killcaff-777")))
            assert ("kill", "-TERM", "777") not in fake_runner.calls
            view = app.display_state()
            started = asyncio.Event()

            async def language():
                started.set()
                await app.action_toggle_language()

            switch = asyncio.create_task(language())
            await asyncio.wait_for(started.wait(), 5)
            assert not switch.done() and old_health.is_attached
            assert app.display_state() == view
        finally:
            blocker.release.set()
            await task
            if switch is not None:
                await switch
        await settled(app, pilot)
        assert not old_health.is_attached and not app._mutation_lock.locked()


def test_save_state_replace_failure_preserves_original_and_removes_temp(monkeypatch, tmp_paths):
    snapshot = state.new_state()
    state.save_state(snapshot)
    original = state_path().read_bytes()
    failure = OSError("replace denied")

    def replace(source, target):
        raise failure

    monkeypatch.setattr(state.os, "replace", replace)
    snapshot["quit_apps"] = ["/Demo.app"]
    with pytest.raises(state.StateError, match="replace denied") as error:
        state.save_state(snapshot)
    assert error.value.__cause__ is failure
    assert state_path().read_bytes() == original
    assert not list(tmp_paths.glob("*.tmp"))


def test_time_machine_missing_capability_displays_unavailable(monkeypatch):
    monkeypatch.setattr(launchd, "command_path", lambda name: None)
    info = launchd.time_machine_info()
    rendered = "\n".join(launchd.service_information({"time_machine": info}))
    assert not info["available"]
    assert t("services.tm_unavailable") in rendered
    assert t("services.tm_idle") not in rendered

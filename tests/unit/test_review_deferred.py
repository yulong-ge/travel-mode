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
        'pid 777(caffeinate): PreventUserIdleSystemSleep named: "test"\n', "", 0
    )
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        "777 1 S Thu Oct 15 12:00:00 2026 /usr/bin/caffeinate\n", "", 0
    )
    fake_runner.responses[("ps", "-ww", "-axo", "pid=,command=")] = CmdResult(
        "777 /usr/bin/caffeinate -i\n", "", 0
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
    await app.workers.wait_for_complete()
    await pilot.pause()


class ButtonQueueProbe(Message):
    """A marker behind the second press in the button's message queue."""


async def queued(button, probe_seen):
    button.post_message(ButtonQueueProbe())
    async with asyncio.timeout(5):
        await probe_seen.wait()


async def test_tui_batch_then_off_button_events_are_serial(action_ui, fake_runner, monkeypatch):
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
        assert len(st["frozen_apps"]) == 2
        return 2, 0

    monkeypatch.setattr(system_apps, "freeze_app", freeze)
    monkeypatch.setattr(flow_restore, "run_restore", restore)
    app = TravelApp()
    probe_seen = asyncio.Event()
    async with app.run_test(
        message_hook=lambda message: probe_seen.set() if isinstance(message, ButtonQueueProbe) else None
    ) as pilot:
        await settled(app, pilot)
        app.query_one("#b-apps-all", Button).press()
        try:
            await entered(blocker)
            app.query_one("#b-off", Button).press()
            await queued(app.query_one("#b-off", Button), probe_seen)
            assert "off" not in order
        finally:
            blocker.release.set()
        await settled(app, pilot)
        # The old message pump serializes these actual Button.Pressed events.
        # With action admission, the busy off button rejects this press entirely.
        assert order in (["freeze", "freeze", "off"], ["freeze", "freeze"])


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

    def single(*args):
        order.append("single")
        return True

    monkeypatch.setattr(flow_restore, "run_restore", restore)
    monkeypatch.setattr(system_apps, "freeze_app", single)
    monkeypatch.setattr(system_apps, "grace_quit", single)
    app = TravelApp()
    probe_seen = asyncio.Event()
    async with app.run_test(
        message_hook=lambda message: probe_seen.set() if isinstance(message, ButtonQueueProbe) else None
    ) as pilot:
        await settled(app, pilot)
        app.query_one("#b-off", Button).press()
        try:
            await entered(blocker)
            app.query_one(f"#btn-1-{action}", Button).press()
            await queued(app.query_one(f"#btn-1-{action}", Button), probe_seen)
            assert not order
        finally:
            blocker.release.set()
        await settled(app, pilot)
        assert order in (["off", "single"], ["off"])


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
    app = TravelApp()
    originals = [row["path"] for row in action_ui]
    async with app.run_test() as pilot:
        await settled(app, pilot)
        task = asyncio.create_task(app.act_apps_all(app.query_one("#b-apps-all")))
        try:
            await entered(blocker)
            action_ui[:] = [dict(action_ui[1], name="Replacement", path="/Replacement.app")]
            await app.action_toggle_language()
            await app.workers.wait_for_complete()
            assert app.app_rows[0]["path"] == "/Replacement.app"
        finally:
            blocker.release.set()
            await task
        await settled(app, pilot)
        assert frozen == originals
        assert app.st["frozen_apps"] == originals
        assert not app.app_rows[0]["done"]


async def test_tui_double_click_admits_one_mutation(action_ui, fake_runner):
    command = ("kill", "-TERM", "777")
    fake_runner.responses[command] = CmdResult("", "", 0)
    blocker = BlockingRunner(fake_runner, command)
    shell.set_runner(blocker)
    app = TravelApp()
    async with app.run_test() as pilot:
        await settled(app, pilot)
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
    app = TravelApp()
    async with app.run_test() as pilot:
        await settled(app, pilot)
        button = app.query_one("#b-killcaff-777", Button)
        button.press().press()
        await settled(app, pilot)
        assert fake_runner.calls.count(command) == 1


def test_time_machine_unparseable_status_is_explicit_unknown(monkeypatch, fake_runner):
    monkeypatch.setattr(launchd.shutil, "which", lambda name: "/usr/bin/tmutil")
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
    with pytest.raises(RuntimeError, match=r"ps -p 777.*exit 2.*denied"):
        launchd.svc_scan(directories=(tmp_paths,))


async def test_tui_caffeinate_failure_never_logs_success(action_ui, fake_runner):
    command = ("kill", "-TERM", "777")
    fake_runner.responses[command] = CmdResult("", "denied", 2)
    app = TravelApp()
    async with app.run_test() as pilot:
        await settled(app, pilot)
        messages = []
        app.logline = lambda *args: messages.append(args)
        with pytest.raises(RuntimeError, match=r"kill.*exit 2.*denied"):
            await app._dispatch_button(Button.Pressed(app.query_one("#b-killcaff-777")))
        assert not any(kind == "ok" for kind, _ in messages)


def test_display_sleep_failure_is_explicit(monkeypatch, fake_runner):
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": [], "kept": []})
    monkeypatch.setattr(network_api, "configured_names", lambda: [])
    monkeypatch.setattr(flow_apply, "ask", lambda message, **kw: "displaysleepnow" in message)
    fake_runner.responses[("pmset", "displaysleepnow")] = CmdResult("", "denied", 3)
    with pytest.raises(RuntimeError, match=r"pmset displaysleepnow.*exit 3.*denied"):
        flow_apply.mode_apply(yes=True)


@pytest.mark.parametrize("first", ["b-apps-all", "b-off"])
async def test_tui_transaction_disables_mutations_across_recompose(
    first, action_ui, fake_runner, monkeypatch
):
    command = ("test-transaction",)
    fake_runner.responses[command] = CmdResult("", "", 0)
    blocker = BlockingRunner(fake_runner, command)
    shell.set_runner(blocker)
    snapshot = state.new_state()
    snapshot["quit_apps"] = [action_ui[0]["path"]]
    state.save_state(snapshot)

    def freeze(path):
        shell.run_rc(list(command))
        return True

    def restore(st, emit):
        shell.run_rc(list(command))
        return 1, 0

    monkeypatch.setattr(system_apps, "freeze_app", freeze)
    monkeypatch.setattr(flow_restore, "run_restore", restore)
    app = TravelApp()
    async with app.run_test() as pilot:
        await settled(app, pilot)
        task = asyncio.create_task(app.on_button_pressed(Button.Pressed(app.query_one(f"#{first}"))))
        try:
            await entered(blocker)
            assert app._mutation_lock.locked()
            assert t("impl.executing_action", action=app._mutation_action) == app.sub_title
            assert all(
                button.disabled
                for button in app.query(Button)
                if button.id not in {"b-dash-apps", "b-dash-svc"}
            )
            # A second dispatcher invocation cannot mutate even with a fresh synthetic button.
            await app.on_button_pressed(Button.Pressed(Button(id="b-killcaff-777")))
            assert ("kill", "-TERM", "777") not in fake_runner.calls
            view = app.display_state()
            await app.action_toggle_language()
            await app.workers.wait_for_complete()
            assert app.display_state() == view
            assert t("impl.executing_action", action=app._mutation_action) == app.sub_title
            assert all(
                button.disabled
                for button in app.query(Button)
                if button.id not in {"b-dash-apps", "b-dash-svc"}
            )
        finally:
            blocker.release.set()
            await task
        await settled(app, pilot)
        assert not app._mutation_lock.locked()
        assert not app.query_one("#b-apps-all", Button).disabled


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
    monkeypatch.setattr(launchd.shutil, "which", lambda name: None)
    info = launchd.time_machine_info()
    rendered = "\n".join(launchd.service_information({"time_machine": info}))
    assert not info["available"]
    assert t("services.tm_unavailable") in rendered
    assert t("services.tm_idle") not in rendered

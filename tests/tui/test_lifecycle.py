"""Barriers expose late callbacks without depending on CPU speed or sleeps."""

import asyncio
import threading

import pytest
from textual.widgets import RichLog

from travel_mode.actions.power import EnableSleep
from travel_mode.system.shell import CmdResult, ReadError
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout


def blocked_action(app, monkeypatch):
    entered = asyncio.Event()
    release, finished = threading.Event(), threading.Event()

    def execute(action, context=None):
        assert isinstance(action, EnableSleep)
        app.call_from_thread(entered.set)
        if not release.wait(30):
            raise TimeoutError("fixture action was not released")
        app.tlog("ok", "fixture action completed")
        finished.set()
        return "", 0

    monkeypatch.setattr(app.executor, "execute", execute)
    return entered, release, finished


async def test_click_waits_for_button_handler_not_worker_snapshot(fake_runner, monkeypatch):
    fake_runner.responses[("pmset", "-g")] = CmdResult("SleepDisabled 1\n", "", 0)
    app = TravelApp(scenario="away")
    entered, release, _ = blocked_action(app, monkeypatch)
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        mouse_returned = asyncio.Event()
        original = pilot.click

        async def click(*args, **kwargs):
            result = await original(*args, **kwargs)
            mouse_returned.set()
            return result

        monkeypatch.setattr(pilot, "click", click)
        interaction = asyncio.create_task(click_after_layout(pilot, "#ready-0"))
        try:
            await asyncio.wait_for(entered.wait(), 30)
            await asyncio.wait_for(mouse_returned.wait(), 30)
            # Intentionally demonstrate why the former CI wait was insufficient.
            await app.workers.wait_for_complete()
            assert app._mutation_lock.locked() and app._pending_inputs
            assert not interaction.done()
        finally:
            release.set()
            await asyncio.wait_for(interaction, 30)
        assert not app._mutation_lock.locked() and not app._pending_inputs
        assert app.refresh_complete


@pytest.mark.parametrize("cancel_await", [False, True])
async def test_shutdown_drains_actions_before_unmounting_log(cancel_await, fake_runner, monkeypatch):
    fake_runner.responses[("pmset", "-g")] = CmdResult("SleepDisabled 1\n", "", 0)
    app = TravelApp(scenario="away")
    entered, release, finished = blocked_action(app, monkeypatch)
    shutdown_started = asyncio.Event()
    original_shutdown, original_close = app._shutdown, app._close_all

    async def shutdown():
        shutdown_started.set()
        await original_shutdown()

    async def close():
        assert finished.is_set()
        assert app.query_one("#log", RichLog).is_attached
        assert not app._action_threads and not app._pending_inputs
        await original_close()

    async def release_on_shutdown():
        await asyncio.wait_for(shutdown_started.wait(), 30)
        release.set()

    monkeypatch.setattr(app, "_shutdown", shutdown)
    monkeypatch.setattr(app, "_close_all", close)
    async with app.run_test(size=(140, 55)):
        await app.wait_for_idle()
        if cancel_await:
            action = asyncio.create_task(app.busy(app.query_one("#ready-0"), app.execute, EnableSleep()))
        else:
            app.query_one("#ready-0").press()
        try:
            await asyncio.wait_for(entered.wait(), 30)
            if cancel_await:
                action.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await action
        except BaseException:
            release.set()
            raise
        releaser = asyncio.create_task(release_on_shutdown())
    await releaser
    assert finished.is_set() and app._exception is None


async def test_passive_measurement_queries_stay_on_ui_thread_and_language_waits_for_actions(
    fake_runner, monkeypatch
):
    from travel_mode import measurement

    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    app = TravelApp(scenario="work")
    entered = asyncio.Event()
    release = threading.Event()
    original_query = app.query_one

    def query(*args, **kwargs):
        assert threading.current_thread() is threading.main_thread()
        return original_query(*args, **kwargs)

    def runner(command, timeout=None, interactive=False):
        if command[:2] == ["sudo", "pmset"]:
            app.call_from_thread(entered.set)
            if not release.wait(30):
                raise TimeoutError("fixture action was not released")
        return fake_runner(command, timeout, interactive)

    from travel_mode.system import shell

    shell.set_runner(runner)
    monkeypatch.setattr(app, "query_one", query)
    monkeypatch.setattr(
        measurement, "begin", lambda *a, **kw: pytest.fail("TUI must not block on measurement")
    )
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        from tui_plan import select_plan

        await select_plan(app, ids={r["id"] for r in app.plan_items if r["descriptor"]["kind"] == "power"})
        app.query_one("#q-measure").value = True
        action = asyncio.create_task(click_after_layout(pilot, "#b-quick"))
        language_started = asyncio.Event()

        async def language():
            language_started.set()
            await app.action_toggle_language()

        switch = None
        generation = app._refresh_generation
        try:
            await asyncio.wait_for(entered.wait(), 30)
            switch = asyncio.create_task(language())
            await asyncio.wait_for(language_started.wait(), 30)
            assert not switch.done() and app._refresh_generation == generation
        finally:
            release.set()
            await asyncio.wait_for(action, 30)
            if switch is not None:
                await asyncio.wait_for(switch, 30)
        await app.wait_for_idle()
        assert app.st["measurement"]["mode"] == "passive"
        assert app.st["measurement"]["after"] is None
        assert app._exception is None


async def test_cancelled_audit_error_cannot_access_recomposed_controls(monkeypatch):
    from travel_mode.system import processes

    app = TravelApp(scenario="away")
    entered = asyncio.Event()
    release, returned = threading.Event(), threading.Event()

    def read():
        app.call_from_thread(entered.set)
        try:
            if not release.wait(30):
                raise TimeoutError("fixture audit was not released")
            raise ReadError("fixture audit denied")
        finally:
            returned.set()

    monkeypatch.setattr(processes, "power_top", read)
    async with app.run_test(size=(140, 55)):
        await app.wait_for_idle()
        app.refresh_audit_detail()
        try:
            await asyncio.wait_for(entered.wait(), 30)
            await app.action_toggle_language()
        finally:
            release.set()
            assert await asyncio.to_thread(returned.wait, 30)
        await app.wait_for_idle()
        assert app.refresh_complete and app.query_one("#log").is_attached
        assert app._exception is None


async def test_shutdown_waits_for_cancelled_read_worker_cleanup():
    app = TravelApp(scenario="away")
    started, cleaning, release, finished = (asyncio.Event() for _ in range(4))

    async def read():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            finished.set()

    async def release_cleanup():
        await asyncio.wait_for(cleaning.wait(), 30)
        release.set()

    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        app.run_worker(read, group="dash")
        await asyncio.wait_for(started.wait(), 30)
        releaser = asyncio.create_task(release_cleanup())
    await releaser
    assert finished.is_set() and app._exception is None

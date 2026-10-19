"""Password admission completes by callbacks, without prompts inside raw-mode UI."""

import asyncio
from contextlib import contextmanager

import pytest
from textual.widgets import Button, Static

from travel_mode import sessions, state
from travel_mode.i18n import set_language, t
from travel_mode.system import shell
from travel_mode.tui.administrator import AdministratorScreen
from travel_mode.tui.app import TravelApp
from tui_interaction import modal_result
from tui_plan import select_plan


@contextmanager
def fake_terminal(monkeypatch, app, runner, success):
    suspended = []
    active = False
    original = runner.__class__.__call__
    from unittest.mock import patch

    messages = []

    def terminal_print(message, *, flush):
        assert active and flush, "explanation must print in the suspended terminal"
        messages.append(message)

    @contextmanager
    def suspend():
        nonlocal active
        active = True
        suspended.append("enter")
        try:
            yield
        finally:
            active = False
            suspended.append("leave")

    def invoke(self, command, timeout=None, interactive=False):
        if command[:2] == ["sudo", "-v"]:
            assert command == ["sudo", "-v", "-p", ""]
            assert messages == [t("admin.terminal", names=t("admin.restore_spotlight"))]
            assert active and interactive, "authentication must run only in the suspended terminal"
            if success == "interrupt":
                raise KeyboardInterrupt
        return original(self, command, timeout, interactive)

    monkeypatch.setattr(app, "suspend", suspend)
    monkeypatch.setattr(runner.__class__, "__call__", invoke)
    runner.responses[("sudo", "-v")] = shell.CmdResult(
        "", "" if success else "cancelled", 0 if success else 1
    )
    with patch("builtins.print", terminal_print):
        yield suspended


async def start_action(pilot, button_id, monkeypatch):
    app = pilot.app
    mounted = asyncio.get_running_loop().create_future()
    original = app.push_screen

    def push(screen, *args, **kwargs):
        result = original(screen, *args, **kwargs)
        if isinstance(screen, AdministratorScreen):
            mounted.set_result(screen)
        return result

    monkeypatch.setattr(app, "push_screen", push)
    button = app.main_query_one(button_id)
    button.press()
    dialog = await asyncio.wait_for(mounted, 10)
    monkeypatch.setattr(app, "push_screen", original)
    await asyncio.wait_for(dialog.mounted.wait(), 10)
    assert await dialog.wait_for_refresh()
    return dialog


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("choice", ["enter", "skip", "cancel", "failed-auth", "cancel-auth"])
async def test_restore_password_decision_keeps_other_items_and_retry_available(
    fake_runner, monkeypatch, tmp_path, language, choice, capsys
):
    set_language(language)
    fake_runner.responses[("sudo", "-n", "-l", "mdutil")] = shell.CmdResult("", "password required", 1)
    fake_runner.responses[("sudo", "mdutil")] = shell.CmdResult("", "", 0)
    fake_runner.responses[("open",)] = shell.CmdResult("", "", 0)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        sessions.begin(app.st)
        app.st.update(spotlight_off="1", quit_apps=["Fixture"])
        app.save()
        app.merged = True
        app.view_mode = "session"
        app.update_primary()
        before = list(fake_runner.calls)
        with fake_terminal(
            monkeypatch, app, fake_runner, "interrupt" if choice == "cancel-auth" else choice != "failed-auth"
        ) as suspended:
            dialog = await start_action(pilot, "#b-off", monkeypatch)
            assert t("admin.restore_spotlight") in dialog.names
            assert fake_runner.calls[len(before) :] == [("sudo", "-n", "-l", "mdutil", "-i", "on", "/")]
            with modal_result(monkeypatch, app, AdministratorScreen):
                # This dialog was already mounted; dismissal is followed by the
                # admitted button completion, including restore/report callbacks.
                dialog.query_one(
                    "#admin-" + ("enter" if choice in {"failed-auth", "cancel-auth"} else choice), Button
                ).press()
            await asyncio.wait_for(app.wait_for_idle(), 10)
        assert app.is_running and app._exception is None
        calls = fake_runner.calls[len(before) :]
        assert suspended == (["enter", "leave"] if choice in {"enter", "failed-auth", "cancel-auth"} else [])
        assert (
            ("open", "-a", "Fixture") in calls
            if choice != "cancel"
            else not any(c[0] == "open" for c in calls)
        )
        if choice in {"skip", "failed-auth", "cancel-auth"}:
            st = state.load_state()
            assert st["spotlight_off"] == "1" and not st["quit_apps"]
            assert st["session"]["restored"] == 1 and st["session"]["restore_failures"] == 1
            failure = st["action_failures"][-1]
            assert failure["kind"] == "not_executed" and failure["rc"] == 77
            assert failure["action"] == t("admin.restore_spotlight")
            assert ("sudo", "mdutil", "-i", "on", "/") not in calls
            assert app.main_query_one("#restore-retry-spotlight_off").is_attached
            assert t("admin.password_reason") in str(
                app.main_query_one("#restore-pending Static", Static).content
            )
            assert "timeout" not in app.last_result
        elif choice == "cancel":
            assert state.load_state()["quit_apps"] == ["Fixture"]
            assert not state.load_state().get("action_failures")
        else:
            assert ("sudo", "mdutil", "-i", "on", "/") in calls
            assert state.load_state() is None
    output = capsys.readouterr()
    assert "Password:" not in output.out + output.err
    assert "Traceback" not in output.out + output.err


@pytest.mark.parametrize("kind", ["plan", "repair"])
async def test_apply_and_single_repair_use_same_password_admission(fake_runner, monkeypatch, kind):
    fake_runner.responses[("sudo", "-n", "-l")] = shell.CmdResult("", "password required", 1)
    fake_runner.responses[("brightness", "0.5")] = shell.CmdResult("", "", 0)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        if kind == "plan":
            await select_plan(app, group="power")
            button_id = "#b-quick"
        else:
            # A real health button exercises the shared descriptor factory.
            from travel_mode.tui.widgets import Button as ActionButton

            button = ActionButton("fixture", id="b-health-fixture-0")
            button.health_descriptor = dict(kind="sleep")
            await app.main_query_one("#dash-health").mount(button)
            button_id = "#b-health-fixture-0"
        dialog = await start_action(pilot, button_id, monkeypatch)
        dialog.query_one("#admin-skip", Button).press()
        await asyncio.wait_for(app.wait_for_idle(), 10)
        assert app.is_running
        assert app.st["session"]["actions"]["failed"] > 0
        assert all(r["kind"] == "not_executed" for r in app.st["action_failures"])
        assert not any(c[:2] in [("sudo", "pmset"), ("sudo", "-v")] for c in fake_runner.calls)


async def test_pending_spotlight_retry_repeats_password_flow_and_finishes_session(fake_runner, monkeypatch):
    fake_runner.responses[("sudo", "-n", "-l", "mdutil")] = shell.CmdResult("", "password required", 1)
    fake_runner.responses[("sudo", "mdutil")] = shell.CmdResult("", "", 0)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.wait_for_idle()
        sessions.begin(app.st)
        app.st["spotlight_off"] = "1"
        app.save()
        app.merged = True
        app.view_mode = "session"
        app.update_primary()
        dialog = await start_action(pilot, "#b-off", monkeypatch)
        dialog.query_one("#admin-skip", Button).press()
        await asyncio.wait_for(app.wait_for_idle(), 10)
        assert app.main_query_one("#restore-retry-spotlight_off").is_attached
        with fake_terminal(monkeypatch, app, fake_runner, True) as suspended:
            dialog = await start_action(pilot, "#restore-retry-spotlight_off", monkeypatch)
            dialog.query_one("#admin-enter", Button).press()
            await asyncio.wait_for(app.wait_for_idle(), 10)
        assert suspended == ["enter", "leave"]
        assert state.load_state() is None
        report = sessions.read_history()[-1]
        assert report["restore"]["failed_attempts"] == 1
        assert report["restore"]["restored"] == 1
        assert report["failures"][-1]["recovered"]


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 45)])
@pytest.mark.parametrize("language", ["en", "zh"])
async def test_administrator_dialog_controls_fit_and_list_is_scrollable(fake_runner, size, language):
    set_language(language)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=size):
        await app.wait_for_idle()
        dialog = AdministratorScreen(
            [(["fixture", str(i)], t("admin.restore_daemon", name=f"Fixture {i}")) for i in range(25)]
        )
        app.push_screen(dialog)
        await asyncio.wait_for(dialog.mounted.wait(), 10)
        assert await dialog.wait_for_refresh()
        instruction = dialog.query_one("#admin-instructions", Static)
        assert str(instruction.content) == t("admin.instructions")
        assert dialog.region.contains_region(instruction.region)
        for button in dialog.query(Button):
            assert dialog.region.contains_region(button.region)
            assert button.label.cell_length <= button.content_region.width
        dialog.dismiss("cancel")


async def test_retry_one_item_keeps_other_pending_restore_receipts(fake_runner, monkeypatch):
    for tool in ("mdutil", "networksetup"):
        fake_runner.responses[("sudo", "-n", "-l", tool)] = shell.CmdResult("", "password required", 1)
    fake_runner.responses[("sudo", "mdutil")] = shell.CmdResult("", "", 0)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        sessions.begin(app.st)
        app.st.update(spotlight_off="1", wifi_off="en0")
        app.save()
        app.merged = True
        app.view_mode = "session"
        app.update_primary()
        dialog = await start_action(pilot, "#b-off", monkeypatch)
        dialog.query_one("#admin-skip", Button).press()
        await asyncio.wait_for(app.wait_for_idle(), 10)
        with fake_terminal(monkeypatch, app, fake_runner, True):
            dialog = await start_action(pilot, "#restore-retry-spotlight_off", monkeypatch)
            dialog.query_one("#admin-enter", Button).press()
            await asyncio.wait_for(app.wait_for_idle(), 10)
        snapshot = state.load_state()
        assert snapshot is not None and snapshot["wifi_off"] == "en0"
        assert not snapshot["spotlight_off"]
        assert not sessions.read_history()
        assert app.main_query_one("#restore-retry-wifi_off").is_attached
        assert ("sudo", "networksetup", "-setairportpower", "en0", "on") not in fake_runner.calls

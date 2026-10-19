"""Exact assertion owners and independent hibernation selection regressions."""

import os

import pytest

from travel_mode.actions.executor import Executor
from travel_mode.actions.power import KillCaffeinate, SetPowerProfile
from travel_mode.system import processes
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp

QUERY = ("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")
ARGS = ("ps", "-ww", "-axo", "pid=,command=")
BIRTH = "Thu Oct 15 12:00:00 2026"


def caffeinate_fixture(fake_runner):
    from travel_mode.system.health import PROCESS_COMMAND

    fake_runner.responses[tuple(PROCESS_COMMAND)] = CmdResult(
        f"101 50 {os.getuid()} S 0 {BIRTH} /usr/bin/caffeinate\n"
        f"102 50 {os.getuid()} S 0 {BIRTH} /usr/bin/caffeinate\n"
        f"50 1 {os.getuid()} S 0 {BIRTH} /Applications/Terminal.app/Contents/MacOS/Terminal",
        "",
        0,
    )
    for pid, args in [(101, "-i -t 90"), (102, "-d")]:
        fake_runner.responses[("ps", "-ww", "-p", str(pid), "-o", "command=")] = CmdResult(
            "/usr/bin/caffeinate " + args, "", 0
        )
    fake_runner.responses.update(
        {
            ("pmset", "-g", "assertions"): CmdResult(
                "Assertion status system-wide:\n"
                'pid 101(caffeinate): PreventUserIdleSystemSleep named: "test"\n',
                "",
                0,
            ),
            QUERY: CmdResult(
                f"101 50 S {BIRTH} /usr/bin/caffeinate\n"
                f"102 50 S {BIRTH} /usr/bin/caffeinate\n"
                f"50 1 S {BIRTH} /Applications/Terminal.app/Contents/MacOS/Terminal\n",
                "",
                0,
            ),
            ARGS: CmdResult("101 /usr/bin/caffeinate -i -t 90\n102 /usr/bin/caffeinate -d\n", "", 0),
            ("pgrep", "-fl", "caffeinate"): CmdResult("101 caffeinate -i\n102 caffeinate -d\n", "", 0),
            ("kill",): CmdResult("", "", 0),
            ("pkill",): CmdResult("", "", 0),
        }
    )


def test_only_assertion_owner_is_terminated_not_user_caffeinate(fake_runner):
    caffeinate_fixture(fake_runner)
    targets = processes.caffeinate_procs()
    assert [target.process.pid for target in targets] == [101]
    assert "/usr/bin/caffeinate -i -t 90" in targets[0].describe()
    assert "Terminal" in targets[0].describe() and "50" in targets[0].describe()
    assert Executor().execute(KillCaffeinate(targets[0])) == ("", 0)
    assert ("kill", "-TERM", "101") in fake_runner.calls
    assert not any(c[0] == "pkill" or (c[0] == "kill" and c[-1] == "102") for c in fake_runner.calls)


@pytest.mark.parametrize(
    "replacement",
    [
        "101 50 S Fri Oct 16 12:00:00 2026 /usr/bin/caffeinate\n",
        f"101 50 S {BIRTH} /usr/bin/other\n",
    ],
)
def test_caffeinate_pid_reuse_refuses_signal(fake_runner, replacement):
    caffeinate_fixture(fake_runner)
    target = processes.caffeinate_procs()[0]
    fake_runner.responses[QUERY] = CmdResult(replacement, "", 0)
    with pytest.raises(ProcessLookupError, match="101"):
        Executor().execute(KillCaffeinate(target))
    assert not any(c[0] in {"kill", "pkill"} for c in fake_runner.calls)


async def test_hib25_alone_when_original_equals_recommendation(monkeypatch):
    app = TravelApp(scenario="away")
    async with app.run_test():
        await app.wait_for_idle()
        from tui_plan import select_plan

        await select_plan(app, ids={"deep-sleep"})
        calls = []
        monkeypatch.setattr(app, "execute", lambda action: calls.append(action) or ("", 0))
        monkeypatch.setattr(app, "save", lambda: None)
        await app.run_plan(app.query_one("#b-quick"))
        assert len(calls) == 1 and isinstance(calls[0], SetPowerProfile)
        assert calls[0].values == ["hibernatemode", "25"]


async def test_hib25_hidden_when_key_unsupported(fake_runner):
    fake_runner.responses[("pmset", "-g", "cap")] = CmdResult("sleep displaysleep\n", "", 0)
    custom = fake_runner.responses[("pmset", "-g", "custom")].stdout
    import re

    fake_runner.responses[("pmset", "-g", "custom")] = CmdResult(
        re.sub(r"(?m)^.*hibernatemode.*\n?", "", custom), "", 0
    )
    app = TravelApp(scenario="away")
    async with app.run_test() as pilot:
        await app.wait_for_idle()
        await pilot.pause()
        assert not list(app.query("#select-deep-sleep"))


def two_assertion_holders(fake_runner):
    caffeinate_fixture(fake_runner)
    fake_runner.responses[("pmset", "-g", "assertions")] = CmdResult(
        'Assertion status system-wide:\npid 101(caffeinate): PreventUserIdleSystemSleep named: "first"\n'
        'pid 102(caffeinate): PreventUserIdleDisplaySleep named: "second"\n',
        "",
        0,
    )


@pytest.mark.parametrize(
    "yes,aggressive,answers,expected",
    [
        (True, False, [], []),
        (True, True, [], [101, 102]),
        (False, False, ["y", "n"], [101]),
    ],
)
def test_cli_confirms_each_holder_and_yes_requires_aggressive(
    fake_runner, monkeypatch, yes, aggressive, answers, expected
):
    from travel_mode.flows import apply
    from travel_mode.system import apps, launchd, spotlight

    two_assertion_holders(fake_runner)
    monkeypatch.setattr(apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": [], "kept": []})
    monkeypatch.setattr(spotlight, "spotlight_on", lambda: False)
    ask = apply.ask
    prompts = []
    pending = iter(answers)

    def input_answer(prompt):
        prompts.append(prompt)
        return next(pending)

    def select_caffeinate(prompt, **kwargs):
        return ask(prompt, **kwargs) if "caffeinate PID" in prompt else False

    monkeypatch.setattr("builtins.input", input_answer)
    monkeypatch.setattr(apply, "ask", select_caffeinate)
    from travel_mode.i18n import get_language, set_language

    previous = get_language()
    try:
        set_language("en")
        assert apply.mode_apply(yes=yes, aggressive=aggressive) == 0
    finally:
        set_language(previous)
    assert [int(c[-1]) for c in fake_runner.calls if c[0] == "kill"] == expected
    assert len(prompts) == (0 if yes else 2)
    if prompts:
        assert "PID 101" in prompts[0] and "PID 102" in prompts[1]


async def test_tui_has_one_button_per_holder_and_terminates_only_selected(fake_runner, monkeypatch):
    from textual.widgets import Button

    two_assertion_holders(fake_runner)
    app = TravelApp(scenario="away")
    async with app.run_test() as pilot:
        await app.wait_for_idle()
        from tui_interaction import click_after_layout

        await click_after_layout(pilot, "#b-suggestions")
        assert {b.id for b in app.query("Button") if b.id and b.id.startswith("b-killcaff-")} == {
            "b-killcaff-101",
            "b-killcaff-102",
        }
        monkeypatch.setattr(app, "action_refresh", lambda: None)
        await app.on_button_pressed(Button.Pressed(app.query_one("#b-killcaff-102")))
        assert [c for c in fake_runner.calls if c[0] == "kill"] == [("kill", "-TERM", "102")]


def test_caffeinate_assertion_released_before_confirmation_refuses_signal(fake_runner):
    caffeinate_fixture(fake_runner)
    target = processes.caffeinate_procs()[0]
    fake_runner.responses[("pmset", "-g", "assertions")] = CmdResult("", "", 0)
    with pytest.raises(ProcessLookupError, match="101"):
        Executor().execute(KillCaffeinate(target))
    assert not any(c[0] in {"kill", "pkill"} for c in fake_runner.calls)


def test_assertion_owner_name_without_caffeinate_executable_is_not_targeted(fake_runner):
    caffeinate_fixture(fake_runner)
    fake_runner.responses[QUERY] = CmdResult(f"101 50 S {BIRTH} /usr/bin/other\n", "", 0)
    assert processes.caffeinate_procs() == []

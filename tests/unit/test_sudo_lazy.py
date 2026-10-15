"""Lazy credential contracts, using only injected command doubles."""

import pytest

from travel_mode.actions.executor import _sudo_lifecycle
from travel_mode.system import shell
from travel_mode.system.sudo import SudoCredentialError


def test_no_sudo_action_never_probes_or_prompts(fake_runner):
    @_sudo_lifecycle
    def flow():
        return shell.run_rc(["ps", "-axo", "pid,stat,comm"])

    flow()
    assert not any(c[0] == "sudo" for c in fake_runner.calls)


@pytest.mark.parametrize("tty", [True, False])
def test_nopasswd_pmset_never_prompts(tty, monkeypatch, fake_runner):
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    fake_runner.responses[("sudo", "pmset")] = shell.CmdResult("", "", 0)

    @_sudo_lifecycle
    def flow():
        return shell.run_rc(["pmset", "-a", "disablesleep", "0"], sudo=True)

    assert flow()[1] == 0
    assert fake_runner.calls == [
        ("sudo", "-n", "-l", "pmset", "-a", "disablesleep", "0"),
        ("sudo", "pmset", "-a", "disablesleep", "0"),
    ]


@pytest.mark.parametrize("tty", [True, False])
def test_password_required_before_command(tty, monkeypatch, fake_runner):
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    fake_runner.responses.update(
        {
            ("sudo", "-n", "-l"): shell.CmdResult("", "password required", 1),
            ("sudo", "-v"): shell.CmdResult("", "", 0),
            ("sudo", "pmset"): shell.CmdResult("", "", 0),
        }
    )

    @_sudo_lifecycle
    def flow():
        return shell.run_rc(["pmset", "-b", "sleep", "5"], sudo=True)

    if tty:
        assert flow()[1] == 0
        assert fake_runner.calls[1] == ("sudo", "-v")
    else:
        with pytest.raises(SudoCredentialError, match=r"pmset -b sleep 5.*sudo -v.*NOPASSWD"):
            flow()
        assert len(fake_runner.calls) == 1


def test_freeze_action_set_does_not_acquire_sudo(tmp_path, fake_runner):
    from travel_mode.actions.apps import FreezeApp
    from travel_mode.actions.base import Context
    from travel_mode.actions.executor import Executor
    from travel_mode.state import new_state

    app = tmp_path / "Fixture.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    query = ("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")
    fake_runner.responses[query] = shell.CmdResult(
        f"912005 1 S Thu Oct 15 12:00:00 2026 {app}/Contents/MacOS/loop", "", 0
    )
    fake_runner.responses[("kill", "-STOP", "912005")] = shell.CmdResult("", "", 0)

    @_sudo_lifecycle
    def flow():
        return Executor(Context(new_state())).execute(FreezeApp(str(app)))

    assert flow()
    assert not any(c[0] == "sudo" for c in fake_runner.calls)


async def test_tui_uses_same_exact_command_credentials(fake_runner):
    import asyncio

    from travel_mode.actions.executor import Executor
    from travel_mode.state import new_state

    fake_runner.responses[("sudo", "pmset")] = shell.CmdResult("", "", 0)
    snapshot = new_state()
    snapshot["power_origin"] = {"batt_prof": "sleep 3"}

    def no_prompt():
        pytest.fail("NOPASSWD must not suspend the TUI or ask for a password")

    executor = Executor()
    async with executor.transaction(asyncio.Lock(), snapshot, False, lambda *_: None, no_prompt):
        assert (await asyncio.to_thread(shell.run_rc, ["pmset", "-b", "sleep", "5"], sudo=True))[1] == 0
    assert fake_runner.calls[0] == ("sudo", "-n", "-l", "pmset", "-b", "sleep", "5")

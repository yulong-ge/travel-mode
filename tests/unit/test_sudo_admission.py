"""Privilege admission never turns a permission listing into a password prompt."""

import pytest

from travel_mode.actions.base import Context, RecoveryCommand
from travel_mode.actions.executor import Executor
from travel_mode.state import new_state
from travel_mode.system import shell
from travel_mode.system.sudo import Credentials, credential_scope


def test_permission_listing_is_not_passwordless_authorization(fake_runner):
    fake_runner.responses[("sudo", "-n", "-l")] = shell.CmdResult("/usr/bin/mdutil -i on /\n", "", 0)
    fake_runner.responses[("sudo", "-n", "-l", "-l")] = shell.CmdResult(
        "Sudoers entry:\n    Options: authenticate\n    Commands:\n /usr/bin/mdutil -i on /",
        "",
        0,
    )
    fake_runner.responses[("sudo", "-n", "-v")] = shell.CmdResult("", "a password is required", 1)
    credentials = Credentials(interactive=False)
    assert credentials.preflight([(["mdutil", "-i", "on", "/"], "Spotlight")])
    assert not any(c == ("sudo", "-v") for c in fake_runner.calls)


def test_denied_restore_is_recorded_without_running_sudo(fake_runner, tmp_path):
    fake_runner.responses[("sudo", "-n", "-l")] = shell.CmdResult("", "password required", 1)
    st = new_state()
    st["spotlight_off"] = "1"
    with credential_scope(Credentials(interactive=False)):
        output, rc = Executor(Context(st), count_actions=False).execute(
            RecoveryCommand(["mdutil", "-i", "on", "/"], "Spotlight", True)
        )
    assert rc == 77
    assert st["spotlight_off"] == "1"
    assert st["action_failures"][-1]["kind"] == "not_executed"
    assert "timeout" not in output and "Password:" not in output
    assert ("sudo", "mdutil", "-i", "on", "/") not in fake_runner.calls


@pytest.mark.parametrize("interactive", [False, True])
def test_cli_restore_prompts_once_before_privileged_work_or_records_not_executed(
    fake_runner, monkeypatch, interactive, capsys
):
    from travel_mode import sessions, state
    from travel_mode.flows.restore import mode_off

    monkeypatch.setattr("sys.stdin.isatty", lambda: interactive)
    fake_runner.responses[("sudo", "-n", "-l")] = shell.CmdResult("", "password required", 1)
    fake_runner.responses[("sudo", "-v")] = shell.CmdResult("", "", 0)
    fake_runner.responses[("sudo", "mdutil")] = shell.CmdResult("", "", 0)
    fake_runner.responses[("sudo", "networksetup")] = shell.CmdResult("", "", 0)
    fake_runner.responses[("open",)] = shell.CmdResult("", "", 0)
    st = new_state()
    sessions.begin(st)
    st.update(spotlight_off="1", wifi_off="en0", quit_apps=["Fixture"])
    state.save_state(st)
    assert mode_off() == (0 if interactive else 1)
    calls = fake_runner.calls
    assert calls.count(("sudo", "-v")) == int(interactive)
    assert ("open", "-a", "Fixture") in calls
    if interactive:
        assert calls.index(("sudo", "-v")) < calls.index(("sudo", "mdutil", "-i", "on", "/"))
    else:
        assert not any(c[:2] in [("sudo", "mdutil"), ("sudo", "networksetup")] for c in calls)
        assert all(r["kind"] == "not_executed" for r in state.load_state()["action_failures"])
    output = capsys.readouterr()
    assert "Traceback" not in output.out + output.err


def test_real_boundary_forces_noninteractive_sudo_without_changing_logical_argv(monkeypatch):
    from types import SimpleNamespace

    commands = []
    monkeypatch.setattr(shell, "resolve_command", lambda cmd: ["/usr/bin/sudo", *cmd[1:]])
    monkeypatch.setattr(
        shell.subprocess,
        "run",
        lambda cmd, **kwargs: commands.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    logical = ["sudo", "mdutil", "-i", "on", "/"]
    assert shell.subprocess_runner(logical).rc == 0
    assert commands == [["/usr/bin/sudo", "-n", "mdutil", "-i", "on", "/"]]
    assert logical == ["sudo", "mdutil", "-i", "on", "/"]
    # A target's own -n option is not sudo's non-interactive option.
    assert shell.subprocess_runner(["sudo", "fixture", "-n"]).rc == 0
    assert commands[-1] == ["/usr/bin/sudo", "-n", "fixture", "-n"]

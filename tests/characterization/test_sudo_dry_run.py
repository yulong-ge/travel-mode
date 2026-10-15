"""Credential handling uses command doubles, including interactive authentication."""

import pytest

from conftest import FakeRunner
from travel_mode.flows.apply import mode_apply
from travel_mode.system import shell


@pytest.mark.parametrize(
    ("credentials", "tty", "exit_code"), [(True, False, 0), (False, True, 0), (False, False, 1)]
)
def test_dry_run_sudo_credentials(credentials, tty, exit_code, machine, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    real = shell.get_runner()
    auth = FakeRunner({("sudo", "-v"): shell.CmdResult("", "", 0)})
    probe = FakeRunner({("sudo", "-n", "-l"): shell.CmdResult("", "", int(not credentials))})
    denied = FakeRunner({("sudo", "-n", "launchctl", "list"): shell.CmdResult("", "password required", 1)})
    authenticated = credentials

    def runner(cmd, timeout=None, interactive=False):
        nonlocal authenticated
        if cmd[:3] == ["sudo", "-n", "-l"]:
            return probe(cmd, timeout, interactive)
        if cmd == ["sudo", "-v"]:
            assert tty and interactive
            authenticated = True
            return auth(cmd, timeout, interactive)
        if cmd == ["sudo", "-n", "launchctl", "list"] and not authenticated:
            return denied(cmd, timeout, interactive)
        return real(cmd, timeout, interactive)

    shell.set_runner(runner)
    try:
        assert mode_apply(yes=True, dry_run=True) == exit_code
        output = capsys.readouterr()
        assert bool(auth.calls) == (tty and not credentials)
        assert bool(denied.calls) == (not tty and not credentials)
        if exit_code:
            assert "Unable to confirm (requires sudo)" in output.out + output.err
            assert "sudo -n launchctl list" in output.out + output.err
            assert "sudo -v" in output.out + output.err
            assert "Power settings" in output.out
        assert not any(cmd[:2] == ("sudo", "launchctl") for cmd in machine["fake"].calls)
        assert not any(cmd[0] in {"kill", "open", "osascript"} for cmd in machine["fake"].calls)
    finally:
        shell.disable_dry_run()


def test_sudo_read_fails_immediately_with_query_and_remedy(fake_runner):
    fake_runner.responses[("sudo", "-n", "launchctl", "list")] = shell.CmdResult("", "password required", 1)
    with pytest.raises(shell.SudoReadError, match=r"sudo -n launchctl list.*sudo -v"):
        shell._read(["launchctl", "list"], sudo=True)
    assert fake_runner.calls == [("sudo", "-n", "launchctl", "list")]

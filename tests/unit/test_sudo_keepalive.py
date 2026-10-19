"""Phase 6.4 sudo credential refresh lifecycle."""

from __future__ import annotations

from threading import Event

from travel_mode.system.sudo import SudoKeepAlive


def test_sudo_keepalive_starts_and_stops_refresh(monkeypatch):
    calls = []
    refreshed = Event()

    def fake(cmd, timeout=None, sudo=False, interactive=False):
        calls.append((cmd, sudo, interactive))
        if cmd == ["-n", "-v"]:
            refreshed.set()
        return "", 0

    monkeypatch.setattr("travel_mode.system.sudo.run_rc", fake)
    keepalive = SudoKeepAlive(interval=0.01)
    with keepalive:
        assert refreshed.wait(2), "credential refresh worker never ran"
    assert keepalive.thread is None
    assert any(call[0] == ["-v"] and call[1] and call[2] for call in calls)
    assert any(call[0] == ["-n", "-v"] and call[1] for call in calls)


def test_sudo_keepalive_accepts_empty_terminal_prompt(monkeypatch):
    calls = []

    def fake(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return "", 0

    monkeypatch.setattr("travel_mode.system.sudo.run_rc", fake)
    with SudoKeepAlive(prompt=""):
        pass
    assert calls == [(["-v", "-p", ""], dict(sudo=True, interactive=True))]


def test_empty_prompt_dry_run_requires_terminal_and_forwards_exact_argv(fake_runner, monkeypatch):
    from travel_mode.system import shell

    command = ["sudo", "-v", "-p", ""]
    runner = shell.DryRunRunner(fake_runner)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert runner(command, interactive=True).rc == 1
    assert not fake_runner.calls
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    fake_runner.responses[tuple(command)] = shell.CmdResult("", "", 0)
    assert runner(command, interactive=True).rc == 0
    assert fake_runner.calls == [tuple(command)]

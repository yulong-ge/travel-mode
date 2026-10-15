import io
import sys

import travel_mode.flows.restore as flow_restore
import travel_mode.snapshots as snapshots


def test_noninteractive_off_without_snapshot_does_not_restoredefaults(monkeypatch):
    commands = []
    monkeypatch.setattr(snapshots, "load_state", lambda: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(flow_restore, "run_rc", lambda *args, **kwargs: commands.append(args) or ("", 0))
    assert flow_restore.mode_off() == 1
    assert not any("restoredefaults" in command for args in commands for command in args[0])

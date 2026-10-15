"""Opt-in host probes: only read-only Apple commands, with isolated HOME/PATH."""

import sys

import pytest

from travel_mode.system import shell


@pytest.mark.macos
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS system tools")
@pytest.mark.parametrize("command", [["sw_vers", "-productVersion"], ["ioreg", "-rn", "AppleSmartBattery"]])
def test_host_apple_tool_bypasses_same_name_shim(command, tmp_path, monkeypatch):
    shim = tmp_path / command[0]
    shim.write_text("#!/bin/sh\nprintf 'unexpected shim'\nexit 99\n")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert shell.resolve_command(command)[0] == shell.SYSTEM_TOOLS[command[0]]
    result = shell.subprocess_runner(command, timeout=10)
    assert result.rc == 0, result.stderr
    assert "unexpected shim" not in result.stdout
    if command[0] == "sw_vers":
        assert result.stdout.strip()

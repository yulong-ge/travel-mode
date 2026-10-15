import travel_mode.flows.thaw as flow_thaw
from travel_mode.system.shell import CmdResult


def test_thaw_all_selects_stopped_app_processes(monkeypatch, capsys):
    commands = []

    def fake_runner(cmd, timeout=None, interactive=False):
        commands.append(cmd)
        if cmd[:3] == ["ps", "-axo", "pid,stat,comm"]:
            return CmdResult(
                "PID STAT COMMAND\n123 T /Applications/Demo.app/Contents/MacOS/Demo\n"
                "456 S /Applications/Other.app/Contents/MacOS/Other\n"
                "789 T /usr/bin/tool\n",
                "",
                0,
            )
        if cmd[:2] == ["kill", "-CONT"]:
            return CmdResult("", "", 0)
        return CmdResult("", "unexpected", 1)

    from travel_mode.system.shell import set_runner

    set_runner(fake_runner)
    assert flow_thaw.thaw_processes(all_processes=True) == 0
    assert ["kill", "-CONT", "123"] in commands
    assert "123" in capsys.readouterr().out

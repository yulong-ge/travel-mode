from pathlib import Path

from travel_mode.system import pmset
from travel_mode.system.shell import CmdResult, set_runner, subprocess_runner


def test_pmset_parsers_use_fake_runner():
    custom = Path("tests/fixtures/pmset-g-custom.txt").read_text()
    current = Path("tests/fixtures/pmset-g.txt").read_text()

    def fake(cmd, timeout=None):
        if cmd == ["pmset", "-g", "custom"]:
            return CmdResult(custom, "", 0)
        if cmd == ["pmset", "-g"]:
            return CmdResult(current, "", 0)
        return CmdResult("", "unexpected command", 1)

    set_runner(fake)
    try:
        profiles = pmset.pmset_custom()
        assert profiles
        assert pmset.pmset_g()
    finally:
        set_runner(subprocess_runner)

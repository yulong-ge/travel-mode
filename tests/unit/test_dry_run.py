from travel_mode.system.shell import CmdResult, DryRunRunner, dry_run_commands


def test_dry_run_passes_read_only_and_records_mutations(capsys):
    calls = []

    def real(cmd, timeout=None, interactive=False):
        calls.append(cmd)
        return CmdResult("probe\n", "", 0)

    runner = DryRunRunner(real)
    assert runner(["pmset", "-g", "custom"]).stdout == "probe\n"
    result = runner(["sudo", "pmset", "-b", "sleep", "5"])
    assert result.rc == 0
    assert calls == [["pmset", "-g", "custom"]]
    assert "[dry-run] sudo pmset -b sleep 5" in capsys.readouterr().out


def test_dry_run_command_inventory_is_stable():
    assert isinstance(dry_run_commands(), tuple)

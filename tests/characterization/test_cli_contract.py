"""Full ordered command, persisted state and visible output contracts."""

import pytest

import travel_mode.flows.apply as flow_apply
import travel_mode.flows.restore as flow_restore
import travel_mode.snapshots as snapshots
from travel_mode import cli, state
from travel_mode.config import load_config
from travel_mode.profiles import profile_scope
from travel_mode.system import apps as system_apps
from travel_mode.system.shell import CmdResult

from .conftest import artifact, assert_golden


def legacy_template_contract(result):
    """Retain the existing interface normalization; scenario/category changes stay visible."""
    addition = (
        '# Default interface: "tui" or "cli"; overridden by TRAVEL_MODE_INTERFACE, then --tui / --no-tui.\n'
        'interface = "tui"\n'
    )
    assert result["config"].count(addition) == 1
    result["config"] = result["config"].replace(addition, "", 1)
    return result


@pytest.mark.parametrize("case", ["yes", "aggressive", "flight", "overnight", "work", "all", "none", "mixed"])
def test_apply_contract(case, machine, monkeypatch, capsys):
    def answer(prompt):
        print(prompt, end="")
        if case == "none":
            return "n"
        if case == "mixed" and "CPU" in prompt:
            return "f" if "Freeze" in prompt else "s" if "Ask" in prompt else "y"
        return "y"

    monkeypatch.setattr("builtins.input", answer)
    with profile_scope(load_config(), case if case in {"flight", "overnight", "work"} else None):
        rc = flow_apply.mode_apply(yes=case not in {"all", "none", "mixed"}, aggressive=case == "aggressive")
    captured = capsys.readouterr()
    assert_golden(f"apply-{case}", artifact(machine, rc, captured.out + captured.err), machine["root"])


@pytest.mark.parametrize("case", ["complete", "partial", "force", "missing", "merged", "app-only"])
def test_off_contract(case, machine, monkeypatch, capsys):
    if case != "missing":
        snapshot, _ = snapshots.snapshot_origin()
        snapshot["quit_apps"] = [machine["paths"]["Quit Demo"]]
        if case != "app-only":
            snapshot.update(
                batt_prof="sleep 12",
                ac_prof="womp 1",
                sleepdisabled="1",
                brightness="0.8",
                wifi_off="en0",
                bt_off="1",
                spotlight_off="1",
                unloaded_svcs=[str(machine["plist"])],
                unloaded_sys_svcs=[str(machine["daemon"])],
            )
        state.save_state(snapshot)
        if case == "merged":
            merged, exists = snapshots.snapshot_origin()
            assert exists
            merged["quit_apps"].append(machine["paths"]["Ask Demo"])
            state.save_state(merged)
    if case in {"partial", "force"}:
        machine["fake"].responses[("blueutil", "-p", "1")] = CmdResult("fixture failure", "", 2)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = flow_restore.mode_off(force_off=case == "force")
    captured = capsys.readouterr()
    assert_golden(f"off-{case}", artifact(machine, rc, captured.out + captured.err), machine["root"])


COMMANDS = [
    ["check"],
    ["check", "--json"],
    ["report"],
    ["report", "--json"],
    ["suggest"],
    ["suggest", "--json"],
    ["thaw"],
    ["thaw", "--all"],
    ["config", "path"],
    ["config", "validate"],
    ["config", "init"],
    ["config", "init", "--force"],
    ["config", "edit"],
    ["config", "set-language", "zh"],
]


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda args: "-".join(args))
def test_cli_contract(argv, machine, monkeypatch, capsys):
    monkeypatch.setenv("EDITOR", "fixture-editor")
    monkeypatch.setenv("VISUAL", "fixture-editor")
    machine["processes"]["910002"][0] = "T"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = cli.main(argv)
    captured = capsys.readouterr()
    result = artifact(machine, rc, captured.out + captured.err)
    if argv == ["config", "init", "--force"]:
        result = legacy_template_contract(result)
    assert_golden("cli-" + "-".join(argv), result, machine["root"])


@pytest.mark.parametrize("case", ["thaw-accept", "thaw-reject", "config-init-new", "off-frozen"])
def test_additional_contract(case, machine, monkeypatch, capsys):
    if case.startswith("thaw"):
        machine["processes"]["910002"][0] = "T"
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        def answer(prompt):
            print(prompt, end="")
            return "y" if case == "thaw-accept" else "n"

        monkeypatch.setattr("builtins.input", answer)
        rc = cli.main(["thaw"])
    elif case == "config-init-new":
        machine["config"].unlink()
        rc = cli.main(["config", "init"])
    else:
        from travel_mode.actions.journal import mutation_scope

        snapshot, _ = snapshots.snapshot_origin()
        with mutation_scope(snapshot):
            assert system_apps.freeze_app(machine["paths"]["Freeze Demo"])
        state.save_state(snapshot)
        rc = flow_restore.mode_off()
    captured = capsys.readouterr()
    result = artifact(machine, rc, captured.out + captured.err)
    if case == "config-init-new":
        result = legacy_template_contract(result)
    assert_golden(case, result, machine["root"])

"""Ordered commands/state plus explicit config, JSON and restore semantics."""

import json
from importlib.resources import files
from pathlib import Path

import pytest
import tomlkit
from jsonschema import validate

import travel_mode.flows.apply as flow_apply
import travel_mode.flows.restore as flow_restore
import travel_mode.snapshots as snapshots
from travel_mode import cli, state
from travel_mode.config import load_config
from travel_mode.profiles import profile_scope
from travel_mode.system import apps as system_apps
from travel_mode.system.shell import CmdResult

from .conftest import artifact, assert_golden


def assert_template(machine):
    expected = tomlkit.parse(files("travel_mode").joinpath("config_template.toml").read_text()).unwrap()
    assert tomlkit.parse(machine["config"].read_text()).unwrap() == expected
    assert load_config().options.interface == "tui"


def assert_public_json(command, output):
    data = json.loads(output)
    if command in {"check", "suggest"}:
        schema = json.loads(Path(f"docs/json-schema/{command}.schema.json").read_text())
        validate(data, schema)
    if command == "check":
        assert data["battery"]["source"] == "battery" and data["battery"]["percent"] == 87
        assert [(r["kind"], r.get("pid")) for r in data["blockers"]] == [
            ("sleep_disabled", None),
            ("caffeinate", 919999),
            ("assertion", 910005),
            ("audio", None),
        ]
        assert data["resurrected"] == [] and data["snapshot"]["exists"] is False
        assert [
            (r["id"], r["status"], [a["kind"] for a in r["actions"]]) for r in data["health"]["checks"]
        ] == [
            ("sleep_disabled", "hit", ["sleep"]),
            ("keep_awake", "hit", ["caffeinate"]),
            ("assertions", "hit", ["quit"]),
            ("displays", "clear", []),
            ("spotlight", "clear", []),
            ("backup", "clear", []),
            ("low_power", "hit", ["low_power"]),
            ("brightness", "clear", []),
            ("high_cpu", "clear", []),
        ]
        assert all(r["error"] is None for r in data["health"]["checks"])
        assert [(r["key"], r["current"], r["recommended"], r["supported"]) for r in data["power"]] == [
            ("lowpowermode", "0", "1", True),
            ("sleep", "1", "5", True),
            ("displaysleep", "10", "3", True),
            ("powernap", "0", "0", True),
            ("tcpkeepalive", "1", "1", True),
            ("womp", "0", "0", True),
            ("standby", "1", "1", True),
            ("hibernatemode", "3", "3", True),
            ("ttyskeepawake", "1", "1", True),
            ("disksleep", "10", "10", True),
            ("lessbright", "1", "1", True),
            ("networkoversleep", "0", "0", True),
        ]
        assert [(r["name"], r["cpu"], r["default_action"]) for r in data["apps"]] == [
            ("Quit Demo", 1.0, "quit"),
            ("Freeze Demo", 1.0, "freeze"),
            ("Skip Demo", 1.0, "skip"),
            ("Ask Demo", 1.0, "ask"),
            ("Remote Demo", 1.0, "skip"),
        ]
        assert [r["label"] for r in data["services"]["unloadable"]] == ["org.demo.worker"]
        assert data["services"]["candidates"] == [] and data["services"]["kept"] == []
    elif command == "report":
        assert set(data) == {"version", "sleep_segments", "darkwake_reasons"}
        assert data["sleep_segments"] == [
            dict(
                start="2026-10-14 21:00:00",
                end="2026-10-15 07:00:00",
                start_charge=90,
                end_charge=85,
                drain=5,
            )
        ]
        assert data["darkwake_reasons"] == {"RTC": 1}
    else:
        assert command == "suggest"
        assert [(r["pid"], r["type"], r["scope"], r["daemon_app"]) for r in data["assertions"]] == [
            ("910005", "PreventSystemSleep", "system", True)
        ]
        assert data["with_daemon"] == [] and data["warnings"] == []
        assert data["samples"] == 3
        assert data["apps"] == data["services"] == data["daemon_apps"] == []
        assert tomlkit.parse(data["toml"]).unwrap() == {}


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
    assert_golden(f"apply-{case}", artifact(machine, rc), machine["root"])


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
    assert_golden(f"off-{case}", artifact(machine, rc), machine["root"])
    if case in {"partial", "force"}:
        if case == "partial":
            remaining = state.load_state()
            assert remaining["bt_off"] == "1"
            failure = remaining["action_failures"][-1]
            assert not (machine["root"] / "history.jsonl").exists()
            # Retry only the outstanding Bluetooth item; previous successes stay checkpointed.
            machine["fake"].responses[("blueutil", "-p", "1")] = CmdResult("", "", 0)
            machine["fake"].calls.clear()
            assert flow_restore.mode_off() == 0
            assert machine["fake"].calls.count(("blueutil", "-p", "1")) == 1
            assert not any(
                c[:2] in {("open", machine["paths"]["Quit Demo"]), ("sudo", "pmset")}
                for c in machine["fake"].calls
            )
        report = json.loads((machine["root"] / "history.jsonl").read_text().splitlines()[-1])
        if case == "force":
            failure = report["failures"][-1]
        assert (failure["kind"], failure["rc"], failure["target"], failure["command"]) == (
            "action",
            2,
            "restore:bt_off",
            ["blueutil", "-p", "1"],
        )
        assert state.load_state() is None
        assert report["restore"]["failed"] == int(case == "force")
        # This legacy snapshot has no active-session counters; the receipt still survives retry.
        assert report["restore"]["failed_attempts"] == int(case == "force")
        if case == "partial":
            assert report["failures"][-1]["recovered"] is True


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
    original = tomlkit.parse(machine["config"].read_text()).unwrap()
    monkeypatch.setenv("EDITOR", "fixture-editor")
    monkeypatch.setenv("VISUAL", "fixture-editor")
    machine["processes"]["910002"][0] = "T"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = cli.main(argv)
    captured = capsys.readouterr()
    result = artifact(machine, rc)
    if argv == ["config", "init", "--force"]:
        assert_template(machine)
    else:
        if argv == ["config", "set-language", "zh"]:
            original["options"]["language"] = "zh"
        assert tomlkit.parse(machine["config"].read_text()).unwrap() == original
    if "--json" in argv:
        assert_public_json(argv[0], captured.out)
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
    result = artifact(machine, rc)
    if case == "config-init-new":
        assert_template(machine)
    assert_golden(case, result, machine["root"])

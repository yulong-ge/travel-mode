"""The real apply/restore flow runs solely through the sealed machine FakeRunner."""

from travel_mode import cli, state
from travel_mode.config import load_config
from travel_mode.flows.apply import mode_apply
from travel_mode.flows.restore import mode_off
from travel_mode.profiles import profile_scope


def mutation_commands(machine):
    result = []
    for command in machine["fake"].calls:
        bare = tuple(part for part in command if part not in {"sudo", "-n"})
        if bare[:1] == ("pmset",) and bare[1:2] != ("-g",):
            result.append(bare)
        assert bare[:2] not in {("launchctl", "disable"), ("networksetup", "-setairportpower")}
        assert bare[:2] != ("blueutil", "-p") or len(bare) == 2
    return result


def test_work_apply_and_off_preserve_display_network_ac_and_restore_only_changed_key(machine, capsys):
    with profile_scope(load_config(), "work"):
        assert mode_apply(yes=True, aggressive=True) == 0
    assert mutation_commands(machine) == [("pmset", "-b", "lowpowermode", "1")]
    snapshot = state.load_state()
    assert snapshot["scenario"] == "work"
    assert snapshot["batt_prof"] == "lowpowermode 0"
    for key in ("ac_prof", "brightness", "wifi_off", "bt_off", "spotlight_off", "sleepdisabled"):
        assert not snapshot[key]
    assert not any(command[:2] == ("kill", "-TERM") for command in machine["fake"].calls)
    capsys.readouterr()
    assert cli.main(["check", "--json"]) == 0
    import json

    assert json.loads(capsys.readouterr().out)["scenario"] == "work"
    machine["fake"].calls.clear()
    assert mode_off() == 0
    assert mutation_commands(machine) == [("pmset", "-b", "lowpowermode", "0")]
    assert not (machine["root"] / "state.json").exists()


async def test_category_button_journals_each_identity_once_and_off_restores(machine, monkeypatch):
    from travel_mode import categories
    from travel_mode.tui.app import TravelApp

    public = categories.rules()
    # Use fixture identities with the actual production matcher and executor.
    app_rule = next(row for row in public if row["id"] == "google-drive")
    service_rule = next(row for row in public if row["action"] == "unload" and row["category"] == "backup")
    monkeypatch.setattr(
        categories,
        "rules",
        lambda: (
            {**app_rule, "apps": ["Quit Demo"]},
            {**service_rule, "labels": ["org.demo.worker"]},
        ),
    )
    app = TravelApp(scenario="work")
    async with app.run_test(size=(140, 55)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("4")
        await pilot.pause()
        machine["fake"].calls.clear()
        await pilot.click("#b-category-sync")
        await app.workers.wait_for_complete()
        await pilot.pause()
        snapshot = state.load_state()
        assert snapshot["quit_apps"] == [machine["paths"]["Quit Demo"]]
        assert snapshot["unloaded_svcs"] == [str(machine["plist"])]
        assert snapshot["scenario"] == "work"
        assert sum(call[:2] == ("launchctl", "unload") for call in machine["fake"].calls) == 1
        assert sum(call[:1] == ("osascript",) for call in machine["fake"].calls) == 1
    machine["fake"].calls.clear()
    assert mode_off() == 0
    assert any(call == ("launchctl", "load", str(machine["plist"])) for call in machine["fake"].calls)
    assert any(call == ("open", machine["paths"]["Quit Demo"]) for call in machine["fake"].calls)


async def test_work_profile_brightness_is_opt_in_and_uses_configured_target(machine):
    from travel_mode.tui.app import TravelApp

    with machine["config"].open("a") as stream:
        stream.write("\n[profiles.work]\nbrightness=0.42\n")
    app = TravelApp(scenario="work")
    async with app.run_test(size=(140, 55)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        machine["fake"].calls.clear()
        await pilot.click("#b-quick")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert ("brightness", "0.42") in machine["fake"].calls
        assert state.load_state()["brightness"] == "0.8"
        assert mutation_commands(machine) == [("pmset", "-b", "lowpowermode", "1")]


def test_work_quit_failure_does_not_force_quit_or_report_success(machine, monkeypatch):
    import pytest

    monkeypatch.setattr("travel_mode.system.apps.grace_quit", lambda path, name: False)
    with profile_scope(load_config(), "work"), pytest.raises(RuntimeError, match="Quit Demo"):
        mode_apply(yes=True)
    assert not any(call[:2] == ("kill", "-TERM") for call in machine["fake"].calls)

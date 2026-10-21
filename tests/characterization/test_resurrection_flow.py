"""Restart observations and remediation share the real CLI/TUI Action path."""

import json

from travel_mode import cli, resurrection, snapshots, state
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout


def paused_snapshot(machine):
    snapshot, _ = snapshots.snapshot_origin()
    snapshot.update(
        unloaded_svcs=[str(machine["plist"])],
        quit_apps=[machine["paths"]["Quit Demo"]],
        frozen_apps=[machine["paths"]["Freeze Demo"]],
        frozen_pids=[
            {
                "pid": 910002,
                "started": "Thu Oct 15 12:00:00 2026",
                "command": machine["processes"]["910002"][1],
                "path": machine["paths"]["Freeze Demo"],
            }
        ],
    )
    snapshot["confirmed_pauses"] = [
        dict(kind="agent", path=str(machine["plist"])),
        dict(kind="app", path=machine["paths"]["Quit Demo"]),
        dict(kind="pid", **snapshot["frozen_pids"][0]),
    ]
    state.save_state(snapshot)
    return snapshot


def test_check_json_lists_restarts_and_reapply_pauses_with_existing_actions(machine, capsys):
    snapshot = paused_snapshot(machine)
    assert cli.main(["check", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {row["kind"] for row in data["resurrected"]} == {"agent", "app", "pid"}
    machine["fake"].calls.clear()
    assert cli.main(["check", "--reapply"]) == 0
    assert resurrection.detect(state.load_state()) == []
    assert machine["processes"]["910002"][0] == "T"
    assert "910001" not in machine["processes"]
    assert machine["fake"].calls.count(("launchctl", "unload", str(machine["plist"]))) == 1
    assert state.load_state()["frozen_pids"] == snapshot["frozen_pids"]


async def test_tui_overview_reapply_button_uses_same_actions(machine):
    paused_snapshot(machine)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(140, 55)) as pilot:
        # Drain admitted events and every worker generation they create.
        await app.wait_for_idle()
        await pilot.pause()
        assert app.refresh_complete
        assert {row["kind"] for row in app.resurrected} == {"agent", "app", "pid"}
        app._session_admitted = True  # The separate session-decision tests cover explicit consent.
        button = app.query_one("#b-reapply")
        assert button.display and not button.disabled
        await click_after_layout(pilot, "#b-reapply")
        await app.wait_for_idle()
        assert resurrection.detect(app.st) == []
        assert not app.query_one("#b-reapply").display

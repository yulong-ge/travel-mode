"""Pilot admission and action contracts; sampling setup is outside the action window.

Refresh workers are observed separately by the existing progressive-render tests.
Here refresh requests are recorded explicitly so concurrent read-only samples do
not obscure the complete ordered command trace of the admitted action.
"""

import pytest

import travel_mode.snapshots as snapshots
from travel_mode import state
from travel_mode.tui.app import tui
from tui_interaction import click_after_layout, toggle_language
from tui_plan import select_plan

from .conftest import artifact, assert_golden

CASES = [
    "fixsleep",
    "caffeinate",
    "daemon",
    "spotlight",
    "power",
    "quit",
    "freeze",
    "thaw",
    "skip",
    "apps-all",
    "service",
    "services-all",
    "service-spotlight",
    "network",
    "network-daemon",
    "wifi",
    "bluetooth",
    "off",
    "quick",
    "language",
    "dashboard-en",
    "dashboard-zh",
]


@pytest.mark.parametrize("case", CASES)
async def test_tui_action_contract(case, machine, monkeypatch, capsys):
    if case in {"off", "thaw"}:
        snapshot, _ = snapshots.snapshot_origin()
        snapshot["wifi_off"] = "en0" if case == "off" else ""
        if case == "thaw":
            snapshot["frozen_apps"] = [machine["paths"]["Freeze Demo"]]
            snapshot["frozen_pids"] = [
                {
                    "pid": 910002,
                    "path": machine["paths"]["Freeze Demo"],
                    "command": machine["processes"]["910002"][1],
                    "started": "Thu Oct 15 12:00:00 2026",
                }
            ]
            machine["processes"]["910002"][0] = "T"
        state.save_state(snapshot)
    if case == "dashboard-zh":
        from travel_mode.i18n import set_language

        set_language("zh")
    app = tui(return_class=True)(scenario="away", measure=False)
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        app._session_admitted = True
        logs, refreshes = [], []
        original_log = app.logline

        def log(kind, message):
            logs.append([kind, message])
            original_log(kind, message)

        monkeypatch.setattr(app, "logline", log)
        monkeypatch.setattr(app, "action_refresh", lambda: refreshes.append("refresh"))
        button = None
        if case in {"quit", "freeze", "skip"}:
            name, action = {
                "quit": ("Quit Demo", "quit"),
                "freeze": ("Freeze Demo", "freeze"),
                "skip": ("Ask Demo", "skip"),
            }[case]
            await select_plan(app, names={name}, action=action)
            if action != "skip":
                button = app.query_one("#b-quick")
        elif case in {
            "apps-all",
            "services-all",
            "network",
            "network-daemon",
            "power",
            "spotlight",
            "service-spotlight",
            "service",
            "wifi",
            "bluetooth",
            "quick",
        }:
            if case in {"apps-all", "services-all", "power"}:
                await select_plan(
                    app, group={"apps-all": "apps", "services-all": "services", "power": "power"}[case]
                )
            elif case in {"network", "network-daemon"}:
                await select_plan(
                    app, names={"Network Demo" if case == "network" else "Remote Demo"}, action="quit"
                )
            elif case == "service":
                await select_plan(
                    app, ids={next(r["id"] for r in app.plan_items if r["descriptor"]["kind"] == "agent")}
                )
            elif case == "quick":
                await select_plan(app)
            else:
                await select_plan(app, ids={"spotlight" if case == "service-spotlight" else case})
            button = app.query_one("#b-quick")
        elif case in {"fixsleep", "daemon"}:
            button = next(
                b
                for b in app.query("#sleep-ready Button")
                if b.health_descriptor["kind"] == ("sleep" if case == "fixsleep" else "quit")
            )
        elif case == "caffeinate":
            button = app.query_one("#b-killcaff-919999")
        elif case == "off":
            button = app.query_one("#b-off")
        machine["fake"].calls.clear()
        capsys.readouterr()
        if case == "language":
            await toggle_language(pilot)
        elif case == "thaw":
            from travel_mode.tui.widgets import Button

            button = Button("Freeze Demo", id="thaw-contract")
            button.app_path, button.app_name = machine["paths"]["Freeze Demo"], "Freeze Demo"
            await app.on_button_pressed(Button.Pressed(button))
        elif button is not None:
            await click_after_layout(pilot, "#" + button.id)
        await app.wait_for_idle()
        assert not app._mutation_lock.locked()
        captured = capsys.readouterr()
        result = artifact(machine, None, captured.out + captured.err)
        result.update(log=logs, refresh_requests=refreshes, active=app.view_mode)
        result["rows"] = [
            {k: r[k] for k in ("group", "name", "action", "selected", "state", "operations")}
            for r in app.plan_items
        ]
        assert_golden(f"tui-{case}", result, machine["root"])

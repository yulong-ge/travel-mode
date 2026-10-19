"""Pilot admission and action contracts; sampling setup is outside the action window.

Refresh workers are observed separately by the existing progressive-render tests.
Here refresh requests are recorded explicitly so concurrent read-only samples do
not obscure the complete ordered command trace of the admitted action.
"""

import pytest
from textual.widgets import Checkbox, TabbedContent

import travel_mode.snapshots as snapshots
from travel_mode import state
from travel_mode.tui.app import tui
from tui_interaction import toggle_language

from .conftest import artifact, assert_golden

CASES = [
    ("fixsleep", "1", "b-fixsleep"),
    ("caffeinate", "1", "b-killcaff-919999"),
    ("daemon", "1", "b-health-assertions-0"),
    ("spotlight", "1", "b-spot"),
    ("power", "2", "b-power"),
    ("quit", "3", "btn-0-q"),
    ("freeze", "3", "btn-1-f"),
    ("thaw", "3", "btn-1-f"),
    ("skip", "3", "btn-2-s"),
    ("apps-all", "3", "b-apps-all"),
    ("service", "4", "svc-0"),
    ("services-all", "4", "b-svc-all"),
    ("service-spotlight", "4", "b-svc-spot"),
    ("network", "5", "net-0"),
    ("network-daemon", "5", "net-1"),
    ("wifi", "5", "b-wifi"),
    ("bluetooth", "5", "b-bt"),
    ("off", "5", "b-off"),
    ("quick", "1", "b-quick"),
    ("language", "1", None),
    ("dashboard-en", "1", None),
    ("dashboard-zh", "1", None),
]


@pytest.mark.parametrize("case,page,button", CASES, ids=[item[0] for item in CASES])
async def test_tui_action_contract(case, page, button, machine, monkeypatch, capsys):
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
    app = tui(return_class=True)(scenario="away")
    async with app.run_test(size=(140, 55)) as pilot:
        await app.wait_for_idle()
        if case == "thaw":
            # Consent is covered by test_real_use_tui; this contract isolates
            # the existing session's thaw Action rather than the new decision.
            app._session_admitted = True
            index = next(i for i, row in app.app_rows.items() if row["name"] == "Freeze Demo")
            button = f"btn-{index}-f"
        await pilot.pause()
        await pilot.press(page)
        await pilot.pause()
        if case.startswith("dashboard-"):
            assert {icon.region.width for icon in app.query("#dash-status .status-icon")} == {2}
            assert len({label.region.x for label in app.query("#dash-status .grow")}) == 1
            assert all(
                not str(label.content).startswith(("[yellow]▲", "[green]✔", "[red]✘"))
                for label in app.query("#dash-status .grow")
            )
        logs, refreshes = [], []
        original_log = app.logline

        def log(kind, message):
            logs.append([kind, message])
            original_log(kind, message)

        monkeypatch.setattr(app, "logline", log)
        monkeypatch.setattr(app, "action_refresh", lambda: refreshes.append("refresh"))
        if case == "quick":
            for checkbox in app.query("#p-dash Checkbox"):
                checkbox.value = True
        if case == "power":
            app.query_one("#cb-ac", Checkbox).value = True
            app.query_one("#cb-bright", Checkbox).value = True
            app.query_one("#cb-hib25", Checkbox).value = True
        machine["fake"].calls.clear()
        capsys.readouterr()
        if case == "language":
            await toggle_language(pilot)
        elif not case.startswith("dashboard-"):
            control = app.query_one(f"#{button}")
            control.scroll_visible(animate=False, immediate=True)
            await pilot.pause()
            assert await pilot.click(f"#{button}")
        await pilot.pause()
        await app.wait_for_idle()
        await pilot.pause()
        assert not app._mutation_lock.locked()
        captured = capsys.readouterr()
        result = artifact(machine, None, captured.out + captured.err)
        result.update(log=logs, refresh_requests=refreshes, active=app.query_one(TabbedContent).active)
        # Only stable visible text; log timestamps, header clock and transient toasts are excluded.
        result["labels"] = [
            str(label.content) for label in app.query(f"#{app.query_one(TabbedContent).active} Label")
        ]
        assert_golden(f"tui-{case}", result, machine["root"])

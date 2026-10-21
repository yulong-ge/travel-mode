"""Reproducible public-product examples from the sealed machine, never the host."""

import json
import os
from pathlib import Path

import pytest
from textual.widgets import RichLog

from travel_mode import cli, state
from travel_mode.i18n import set_language
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout


def configure(machine):
    original = machine["paths"]["Quit Demo"]
    path = str(Path(original).with_name("Google Drive.app"))
    Path(original).rename(path)
    machine["paths"]["Quit Demo"] = path
    machine["processes"]["910001"][1] = path + "/Contents/MacOS/Google Drive"
    config = machine["config"].read_text().replace('quit=["Quit Demo"]', 'quit=["Google Drive"]')
    machine["config"].write_text(config)
    command = (
        "osascript",
        "-e",
        "with timeout of 8 seconds",
        "-e",
        'tell application id "org.demo.app910001" to quit',
    )
    machine["fake"].responses[command] = CmdResult(
        "", 'execution error: "Google Drive": User canceled. (-128)', 1
    )


def evidence(machine, interface, scenario, language, output):
    folder = os.environ.get("TM_FAILURE_EVIDENCE")
    if folder:
        root = Path(folder)
        assert root.is_absolute() and root.is_relative_to("/tmp")
        root.mkdir(parents=True, exist_ok=True)
        prefix = root / f"{interface}-{scenario}-{language}"
        prefix.with_suffix(".txt").write_text(output)
        prefix.with_suffix(".json").write_text(json.dumps(state.load_state(), ensure_ascii=False, indent=2))
        prefix.with_suffix(".log").write_bytes((machine["root"] / "travel-mode.log").read_bytes())


@pytest.mark.parametrize("scenario", ["work", "away"])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_cli_google_drive_refusal_example(machine, scenario, language, monkeypatch, capsys):
    configure(machine)
    monkeypatch.setenv("TRAVEL_MODE_LANG", language)
    assert cli.main(["on", "--scenario", scenario, "--yes", "--no-measure"]) == (
        1 if scenario == "work" else 0
    )
    output = capsys.readouterr()
    text = output.out + output.err
    assert "Traceback" not in text
    assert "Google Drive" in text
    assert ("travel-mode.log" in text) is (scenario == "work")
    assert ("910001" in machine["processes"]) is (scenario == "work")
    if scenario == "away":
        assert not state.load_state().get("action_failures")
        assert state.load_state()["session"]["actions"]["failed"] == 0
        assert sum("Google Drive" in line for line in text.splitlines()) == 1
    evidence(machine, "cli", scenario, language, text)


@pytest.mark.parametrize("scenario", ["work", "away"])
@pytest.mark.parametrize("language", ["en", "zh"])
async def test_tui_google_drive_refusal_example(machine, scenario, language):
    configure(machine)
    set_language(language)
    app = TravelApp(scenario=scenario, measure=False)
    async with app.run_test(size=(160, 65)) as pilot:
        await app.wait_for_idle()
        await click_after_layout(pilot, "#b-quick")
        text = "\n".join(line.text for line in app.query_one("#log", RichLog).lines) + "\n"
        assert app.is_running and app._exception is None and "Traceback" not in text
        assert "Google Drive" in text
        # Forced away fallback is a warning; final UI summaries link its details too.
        assert "travel-mode.log" in text
        assert ("910001" in machine["processes"]) is (scenario == "work")
        assert str(machine["plist"]) in state.load_state()["unloaded_svcs"]
        if scenario == "away":
            assert not state.load_state().get("action_failures")
            assert state.load_state()["session"]["actions"] == dict(attempted=8, succeeded=8, failed=0)
            assert sum("Google Drive" in line for line in text.splitlines()) == 1
        evidence(machine, "tui", scenario, language, text)

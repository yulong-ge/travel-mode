"""Session boundaries are explicit before modification, including noninteractive use."""

import pytest

from travel_mode import cli, state


def active(machine):
    assert cli.main(["on", "--scenario", "away", "--yes", "--no-measure"]) == 0
    return state.load_state()


def test_yes_with_active_session_cancels_without_any_modification(machine, capsys):
    previous = active(machine)
    before = (machine["root"] / "state.json").read_bytes()
    machine["fake"].calls.clear()
    capsys.readouterr()
    assert cli.main(["on", "--scenario", "work", "--yes", "--no-measure"]) == 1
    output = capsys.readouterr().out
    assert "travel-mode off" in output and "--continue-session" in output
    assert (machine["root"] / "state.json").read_bytes() == before
    assert state.load_state()["scenario"] == previous["scenario"]
    from travel_mode.failures import mutation

    assert not any(mutation(command) for command in machine["fake"].calls)


def test_continue_preserves_origin_and_records_appended_scenario(machine):
    previous = active(machine)
    assert cli.main(["on", "--scenario", "work", "--yes", "--continue-session", "--no-measure"]) == 0
    result = state.load_state()
    assert result["scenario"] == "away"
    assert result["session"]["started_at"] == previous["session"]["started_at"]
    assert result["session"]["appended_scenarios"] == ["work"]
    assert cli.main(["off"]) == 0
    import json

    report = json.loads((machine["root"] / "history.jsonl").read_text().splitlines()[-1])
    assert report["scenario"] == "away" and report["appended_scenarios"] == ["work"]


def test_default_cli_choice_restores_before_starting_new_session(machine, monkeypatch, capsys):
    active(machine)
    monkeypatch.setattr("builtins.input", lambda prompt: "" if "[1]" in prompt else "n")
    from travel_mode.config import load_config
    from travel_mode.profiles import profile_scope
    from travel_mode.session_conflict import choose_cli

    old = state.load_state()
    with profile_scope(load_config(), None, scenario="work"):
        assert choose_cli(old, "work", yes=False, continue_session=False, emit=lambda *args: None)
    assert state.load_state() is None
    assert cli.main(["on", "--scenario", "work", "--yes", "--no-measure"]) == 0
    assert state.load_state()["scenario"] == "work"
    assert (machine["root"] / "history.jsonl").exists()


@pytest.mark.parametrize("choice", ["cancel", "restore", "continue"])
def test_session_conflict_choices_are_explicit(machine, choice):
    from travel_mode.session_conflict import resolve

    previous = active(machine)
    restored = []
    allowed = resolve(previous, "work", choice=choice, restore=lambda: restored.append(True) or True)
    assert allowed is (choice != "cancel")
    assert restored == ([True] if choice == "restore" else [])
    assert previous["scenario"] == "away"

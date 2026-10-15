"""Scenario admission, action boundaries and error rendering use sealed fixtures."""

import json

import pytest
from textual.widgets import Checkbox, TabbedContent

from travel_mode import cli, state
from travel_mode.config import Config, ConfigError, load_config
from travel_mode.i18n import set_language, t
from travel_mode.paths import config_path, state_path
from travel_mode.profiles import active_scenario, profile_scope
from travel_mode.system import pmset
from travel_mode.tui.app import TravelApp


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_picker_precedes_scans_and_work_selection(language, fake_runner):
    set_language(language)
    app = TravelApp()
    async with app.run_test(size=(80, 24)) as pilot:
        assert not app.query(TabbedContent)
        assert str(app.query_one("#scenario-work").label) == t("scenario.work")
        assert fake_runner.calls == []
        assert not state_path().exists()
        await pilot.click("#scenario-work")
        await app.workers.wait_for_complete()
        assert app.scenario == "work" and app.st["scenario"] == "work"
        assert app.query_one("#q-sync", Checkbox).value
        assert app.query_one("#q-updater", Checkbox).value
        assert not app.query_one("#q-net", Checkbox).value
        assert not app.query_one("#q-spot", Checkbox).value
        assert [key for key, _, _ in app.pw_boxes] == ["lowpowermode"]
    assert active_scenario() == "ask"


@pytest.mark.parametrize("scenario", ["work", "away"])
async def test_configured_scenario_skips_picker(scenario):
    config_path().write_text(f'[options]\nscenario="{scenario}"\n')
    app = TravelApp()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        assert not app.query("#scenario-work")
        assert app.query_one(TabbedContent).active == "p-dash"
        await pilot.pause()


def test_work_recommendations_ignore_sleep_hibernation_overrides(monkeypatch):
    monkeypatch.setattr("travel_mode.system.platform.filter_power_keys", lambda values: values)
    config = Config.defaults()
    config.power_overrides.update(sleep="1", displaysleep="1", hibernatemode="25")
    with profile_scope(config, "work"):
        assert [(key, rec) for key, _, rec in pmset.power_recommendations()] == [("lowpowermode", "1")]
    assert active_scenario() == "ask"


def test_profile_scenario_conflict_is_explicit():
    with (
        pytest.raises(ConfigError, match="--scenario/--profile"),
        profile_scope(Config.defaults(), "flight", scenario="work"),
    ):
        pass


def test_cli_scenario_is_first_question(monkeypatch, darwin_platform, capsys):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "work")
    observed = []
    monkeypatch.setattr(
        "travel_mode.flows.apply.mode_apply", lambda **kw: observed.append(active_scenario()) or 0
    )
    assert cli.main(["--no-tui"]) == 0
    assert prompts == ["  [work/away] "]
    assert observed == ["work"]
    assert capsys.readouterr().out.splitlines()[0] == t("scenario.prompt")


def test_yes_requires_an_explicit_scenario(darwin_platform, capsys):
    assert cli.main(["on", "--yes"]) == 2
    assert "--scenario" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags,expected",
    [(["--profile", "work"], "work"), (["--profile", "flight"], "away"), (["--scenario", "work"], "work")],
)
def test_explicit_profile_or_scenario_skips_cli_question(flags, expected, darwin_platform, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("Unexpected scenario prompt"))
    observed = []
    monkeypatch.setattr(
        "travel_mode.flows.apply.mode_apply", lambda **kw: observed.append(active_scenario()) or 0
    )
    assert cli.main(["--yes", *flags]) == 0
    assert observed == [expected]


@pytest.mark.parametrize(
    "body,key",
    [
        ('[options]\nlanguage="xx"', "options.language"),
        ('[options]\nscenario="bogus"', "options.scenario"),
        ('[categories]\npause=["bogus"]', "categories.pause"),
    ],
)
def test_config_errors_are_single_line_exit_2(body, key, capsys):
    config_path().write_text(body)
    assert cli.main(["check"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert len(captured.err.splitlines()) == 1
    assert str(config_path()) in captured.err and key in captured.err
    assert "travel-mode config validate" in captured.err and "Traceback" not in captured.err


def test_state_error_exit_3_preserves_file(darwin_platform, capsys):
    state_path().write_text("broken snapshot")
    assert cli.main(["check", "--json"]) == 3
    error = capsys.readouterr().err
    assert str(state_path()) in error and "Traceback" not in error
    assert len(error.splitlines()) == 1
    assert state_path().read_text() == "broken snapshot"


def test_tui_startup_state_error_is_rendered_before_textual(darwin_platform, monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    state_path().write_text("broken snapshot")
    assert cli.main(["--tui"]) == 3
    error = capsys.readouterr().err
    assert len(error.splitlines()) == 1 and "Traceback" not in error


def test_debug_preserves_exception_chain(monkeypatch):
    config_path().write_text("bad TOML !")
    monkeypatch.setenv("TRAVEL_MODE_DEBUG", "1")
    with pytest.raises(ConfigError) as error:
        cli.main(["check"])
    assert error.value.__cause__ is not None


def test_unexpected_exception_is_not_converted(monkeypatch):
    def unexpected(argv):
        raise RuntimeError("unexpected fixture failure")

    monkeypatch.setattr(cli, "_main", unexpected)
    with pytest.raises(RuntimeError, match="unexpected fixture failure"):
        cli.main(["check"])


def test_state_scenario_roundtrip_and_check_json(darwin_platform, capsys):
    snapshot = state.new_state()
    snapshot["scenario"] = "work"
    state.save_state(snapshot)
    assert state.load_state()["scenario"] == "work"
    assert cli.main(["check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["scenario"] == "work"


def test_cafe_is_removed():
    assert "cafe" not in load_config().profiles
    with pytest.raises(ConfigError), profile_scope(load_config(), "cafe"):
        pass

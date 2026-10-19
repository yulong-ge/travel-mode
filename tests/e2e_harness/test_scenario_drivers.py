"""Keep the driver policy tied to the product's first question in both languages."""

import pytest
from e2e_ui import answers, select_answer

from travel_mode.cli import ask_scenario
from travel_mode.i18n import set_language, t


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_tui_driver_selects_scenario_before_waiting_for_pages(scenario, tmp_path, monkeypatch):
    from e2e_ui import drive_tui

    class SelectionComplete(Exception):
        pass

    calls = []

    class Session:
        def wait_started(self):
            calls.append("started")

        def wait_text(self, text):
            assert text == t("scenario.prompt")
            calls.append("prompt")

        def shot(self, name):
            calls.append(name)

        def widget_click(self, selector):
            calls.append(selector)

        root = tmp_path
        timeout = 5

        def keys(self, *keys):
            calls.extend(keys)

        def wait_complete(self):
            raise SelectionComplete

    monkeypatch.setattr("e2e_ui.read_layout", lambda path: {"active": "plan"})
    session = Session()
    session.scenario = scenario
    with pytest.raises(SelectionComplete):
        drive_tui(session)
    assert calls == ["started", "prompt", "00-scenario", "scenario-" + scenario, "Enter"]


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("scenario", ["work", "away"])
def test_driver_answers_real_scenario_question(language, scenario, monkeypatch):
    set_language(language)
    policy = answers(scenario)
    observed = []

    def answer(prompt):
        observed.append(prompt)
        key, value = select_answer(prompt, policy)
        assert key == "scenario"
        return value

    monkeypatch.setattr("builtins.input", answer)
    assert ask_scenario() == scenario
    assert observed == [t("scenario.input")]

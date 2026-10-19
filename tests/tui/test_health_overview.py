"""Health rendering and Action admission use committed frames, not timed waits."""

import pytest

from travel_mode.actions import journal
from travel_mode.actions.power import EnableSleep
from travel_mode.i18n import set_language, t
from travel_mode.system import health
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from tui_interaction import click_after_layout


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_health_overview_refresh_and_executor_button(language, fake_runner, monkeypatch):
    set_language(language)
    fake_runner.responses[("pmset", "-g")] = CmdResult("SleepDisabled 1\nlowpowermode 0\n", "", 0)
    fake_runner.responses[tuple(health.native_command("brightness"))] = CmdResult("0.87", "", 0)
    # The fixture has no native/system access. This exact native failure is rendered explicitly.
    fake_runner.responses[tuple(health.native_command("displays"))] = CmdResult("", "probe denied", 1)
    app = TravelApp(scenario="away")
    admitted = []

    def execute(action, context=None):
        assert app._mutation_lock.locked()
        assert journal.active_state() is app.st
        admitted.append(action)
        return "", 0

    monkeypatch.setattr(app.executor, "execute", execute)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        assert app.refresh_complete and "health" in app.pages_ready
        text = "\n".join(
            str(widget.content) for widget in app.query("#sleep-ready Static, #dash-health Static")
        )
        summary = "\n".join(str(w.content) for w in app.query("#screen-heading"))
        assert "SleepDisabled" not in summary
        assert text.count(t("redesign.sleep_disabled")) == 1
        assert "SleepDisabled=1" not in text
        assert len(app.query("#sleep-ready")) == 1
        assert t("health.unknown") in text and "travel-mode.log" in text
        assert len(app.query("#dash-health Button")) == 2
        brightness_button = app.query_one("#b-health-brightness-0")
        assert str(brightness_button.label) == t("health.action.brightness", percent=50)
        assert brightness_button.health_descriptor["value"] == 0.5
        assert "0.5" not in text and "  " not in text
        assert all(len(row["display_actions"]) <= 1 for row in app.plan_data["attention"])
        native_reads = fake_runner.calls.count(tuple(health.native_command("displays")))
        await click_after_layout(pilot, "#b-doctor")
        await app.wait_for_idle()
        assert app.refresh_complete
        assert fake_runner.calls.count(tuple(health.native_command("displays"))) == native_reads + 1
        await click_after_layout(pilot, "#ready-0")
        await app.wait_for_idle()
        assert len(admitted) == 1 and isinstance(admitted[0], EnableSleep)
        assert app.merged and not app._mutation_lock.locked()


async def test_health_language_recomposition_replaces_descriptors_without_stale_mounts(fake_runner):
    app = TravelApp(scenario="away")
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        old = app.query_one("#dash-health")
        await app.action_toggle_language()
        await app.wait_for_idle()
        assert app.refresh_complete and not old.is_attached
        text = "\n".join(
            str(widget.content) for widget in app.query("#sleep-ready Static, #dash-health Static")
        )
        assert t("health_view.low_power_hit") not in text  # Selected power row already covers it.
        await click_after_layout(pilot, "#b-doctor")
        await app.wait_for_idle()
        assert app.refresh_complete


async def test_health_survives_shared_read_failure_and_blocks_mutation(fake_runner, tmp_paths, monkeypatch):
    fake_runner.responses[("pmset", "-g")] = CmdResult("", "read denied", 1)
    app = TravelApp(scenario="away")

    def forbidden(action, context=None):
        pytest.fail("recovery snapshot is unavailable; no Action may execute")

    monkeypatch.setattr(app.executor, "execute", forbidden)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.wait_for_idle()
        assert app.refresh_complete and not app.origin_ready and app._origin_error
        text = "\n".join(str(w.content) for w in app.query("#sleep-ready Static, #dash-health Static"))
        assert "travel-mode.log" in text
        assert t("health.backup.title") not in text  # Normal checks are folded.
        await click_after_layout(pilot, "#b-normal")
        text = "\n".join(str(w.content) for w in app.query("#dash-health Static"))
        assert t("health.backup.title") in text
        assert t("health.unknown") in text
        await click_after_layout(pilot, "#b-doctor")
        await app.wait_for_idle()
        assert app.refresh_complete
        assert all(button.disabled for button in app.query("#dash-health Button") if button.id != "b-normal")
        assert app.query_one("#b-quick").disabled
        assert not (tmp_paths / "state.json").exists()

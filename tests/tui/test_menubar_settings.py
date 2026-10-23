"""The real More-menu callback saves visibility and refreshes after the write."""

import asyncio

import pytest

from travel_mode import menubar
from travel_mode.config import load_config
from travel_mode.i18n import set_language, t
from travel_mode.paths import config_path
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from travel_mode.tui.utility_screen import UtilityScreen
from tui_interaction import click_after_layout, modal_result


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_more_toggles_visibility_after_modal_completion(
    language, size, tmp_path, fake_runner, swiftbar_process, monkeypatch
):
    set_language(language)
    path = config_path()
    path.write_text('# keep settings\n[menubar]\nshow = "always" # visibility\n')
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / menubar.FILENAME).write_bytes(menubar.payload())
    fake_runner.responses[tuple(menubar.PREFERENCE)] = CmdResult(str(folder), "", 0)
    fake_runner.responses[tuple(menubar.REFRESH)] = CmdResult("", "", 0)
    observed = []

    def runner(command, timeout=None, interactive=False):
        if command == menubar.REFRESH:
            observed.append(load_config(path).menubar.show)
        return fake_runner(command, timeout, interactive)

    shell.set_runner(runner)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=size) as pilot:
        await app.wait_for_idle()
        for before, after in (("always", "session"), ("session", "always")):
            with modal_result(monkeypatch, app, UtilityScreen) as completed:
                await click_after_layout(pilot, "#b-more")
                assert await app.screen.wait_for_refresh()
                button = app.screen.query_one("#b-menubar")
                assert str(button.label) == t("menubar.mode_setting", mode=t("menubar.mode_" + before))
                assert button.region.intersection(app.screen.region) == button.region
                assert await pilot.click(button)
                assert await asyncio.wait_for(completed, 30) == "b-menubar"
            await app.wait_for_idle()
            assert app.config.menubar.show == after and load_config(path).menubar.show == after
            assert observed[-1] == after
        assert app._exception is None
    assert observed == ["session", "always"]
    assert "# keep settings" in path.read_text() and "# visibility" in path.read_text()

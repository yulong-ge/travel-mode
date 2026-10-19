"""Plans describe intended effects without claiming observed system mutations."""

import asyncio

import pytest

from travel_mode.actions.runtime import command_lifecycle
from travel_mode.config import load_config
from travel_mode.flows.apply import mode_apply
from travel_mode.i18n import set_language
from travel_mode.profiles import profile_scope


@pytest.mark.parametrize("language", ["en", "zh"])
def test_cli_dry_run_freeze_count_and_projected_power_are_plans(language, machine, capsys):
    set_language(language)
    with profile_scope(load_config(), "work"):
        assert mode_apply(yes=True, dry_run=True) == 0
    output = capsys.readouterr().out
    assert (
        "将冻结 Freeze Demo（1 个进程）" if language == "zh" else "Would freeze Freeze Demo (1 processes)"
    ) in output
    assert (
        "计划用电池时的设置: lowpowermode 1"
        if language == "zh"
        else "Planned battery power settings: lowpowermode 1"
    ) in output
    assert ("将卸载 org.demo.worker" if language == "zh" else "Would unload org.demo.worker") in output
    assert ("将退出 Quit Demo" if language == "zh" else "Would quit Quit Demo") in output
    assert machine["processes"]["910002"][0] == "S"


@pytest.mark.parametrize("language", ["en", "zh"])
def test_tui_dry_run_quit_freeze_and_unload_messages_are_plans(language, machine):
    from travel_mode.tui.app import TravelApp

    set_language(language)
    logs = []
    original_config = machine["config"].read_bytes()

    async def interact():
        app = TravelApp(dry_run=True, scenario="away")
        original_log = app.logline

        def log(kind, message):
            logs.append(message)
            original_log(kind, message)

        app.logline = log
        async with app.run_test(size=(140, 55)) as pilot:
            await app.wait_for_idle()
            await pilot.press("3")
            await pilot.pause()
            freeze_row = next(index for index, row in app.app_rows.items() if row["name"] == "Freeze Demo")
            await pilot.click(f"#btn-{freeze_row}-f")
            await pilot.pause()
            await pilot.click("#btn-0-q")
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            await pilot.click("#svc-0")
            await pilot.pause()

    command_lifecycle(["--dry-run"], lambda argv: asyncio.run(interact()))
    joined = "\n".join(logs)
    for message in (
        "将冻结 Freeze Demo（1 个进程）" if language == "zh" else "Would freeze Freeze Demo (1 processes)",
        "将退出 Quit Demo" if language == "zh" else "Would quit Quit Demo",
        "将卸载 org.demo.worker" if language == "zh" else "Would unload org.demo.worker",
    ):
        assert message in joined
    assert machine["processes"]["910002"][0] == "S"
    assert "910001" in machine["processes"]
    assert machine["config"].read_bytes() == original_config

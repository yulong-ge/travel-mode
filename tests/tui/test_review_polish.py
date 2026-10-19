"""Small-screen prose remains complete in the summary or the details drawer."""

import pytest
from rich.text import Text

from travel_mode.i18n import set_language
from travel_mode.tui.app import TravelApp


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("width", [80, 120, 160])
async def test_satisfied_settings_group_scope_once_and_wrap(language, width):
    from travel_mode.system.pmset import POWER_KEYS

    set_language(language)
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(width, 36)):
        await app.wait_for_idle()
        app.plan_data["satisfied"]["power"] = [
            dict(kind="power", domain="-b", values=[key, value])
            for key, _, value in POWER_KEYS
            if key != "tcpkeepalive"
        ] + [dict(kind="power", domain="-c", values=[key, "0"]) for key in ("powernap", "womp")]
        await app.render_plan()
        assert await app.screen.wait_for_refresh()
        notes = list(app.query(".satisfied"))
        assert len(notes) == 2
        contents = [str(note.content) for note in notes]
        expected = (
            ["Already set on battery:", "Already set on power:"]
            if language == "en"
            else ["已符合（用电池时）：", "已符合（接电时）："]
        )
        for note, content, prefix in zip(notes, contents, expected, strict=True):
            assert content.count(prefix) == 1
            assert note.region.height >= 1
        assert notes[0].region.height > 1
        assert "tcpkeepalive" not in "".join(contents)
        if language == "en":
            assert "Power Nap off, network wake off" in contents[1]
            assert " On" not in contents[0] and " Off" not in contents[0]
        else:
            assert "Power Nap 关闭、网络唤醒关闭" in contents[1]
            assert "," not in contents[0]


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("width", [80, 120, 160])
async def test_long_footer_uses_ellipsis_and_full_sentence_is_in_drawer(language, width):
    from travel_mode.i18n import t

    set_language(language)
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(width, 36)) as pilot:
        await app.wait_for_idle()
        message = t("failure.quit_refused", name="Long Fixture App " * 20)
        app.logline("bad", message)
        assert await app.screen.wait_for_refresh()
        footer = app.query_one("#last-result")
        rendered = footer.render()
        assert isinstance(rendered, Text)
        assert rendered.plain.endswith("…")
        assert rendered.cell_len <= footer.content_region.width
        await pilot.press("d")
        assert await app.screen.wait_for_refresh()
        logged = "".join(line.text for line in app.query_one("#log").lines)
        assert "".join(message.split()) in "".join(logged.split())
        app.logline("ok", "Short result")
        assert await app.screen.wait_for_refresh()
        assert "Short result" not in "".join(line.text for line in app.query_one("#log").lines)


async def test_footer_resize_updates_ellipsis_and_drawer_without_losing_message():
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(80, 36)) as pilot:
        await app.wait_for_idle()
        message = "Fixture action result " * 5
        app.logline("bad", message)
        assert await app.screen.wait_for_refresh()
        footer = app.query_one("#last-result")
        assert footer.render().plain.endswith("…")
        await pilot.resize_terminal(160, 36)
        assert await app.screen.wait_for_refresh()
        assert not footer.render().plain.endswith("…")
        assert not any("Fixture action result" in line.text for line in app.query_one("#log").lines)
        await pilot.resize_terminal(80, 36)
        assert await app.screen.wait_for_refresh()
        assert footer.render().plain.endswith("…")
        logged = "".join(line.text for line in app.query_one("#log").lines)
        assert "".join(message.split()) in "".join(logged.split())


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_display_manual_advice_is_a_sentence_in_the_tui(language, fake_runner):
    from travel_mode.system.health import native_command
    from travel_mode.system.shell import CmdResult

    set_language(language)
    fake_runner.responses[tuple(native_command("displays"))] = CmdResult(
        '[{"id": 99, "builtin": false}]', "", 0
    )
    app = TravelApp(scenario="work", measure=False)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        text = "\n".join(str(w.content) for w in app.query("#dash-health Static"))
        expected = (
            "▲ 外接显示器已连接，会增加耗电。不用时可以断开。"
            if language == "zh"
            else "▲ External display connected; this increases energy use. Disconnect it when not needed."
        )
        assert expected in text
        assert " · " not in text

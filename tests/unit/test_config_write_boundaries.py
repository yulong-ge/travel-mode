"""Configuration canaries and actual TUI language actions stay in isolated paths."""

import asyncio
import hashlib
from pathlib import Path

import pytest
import tomlkit

from travel_mode import cli
from travel_mode.actions.runtime import command_lifecycle
from travel_mode.system.shell import CmdResult
from travel_mode.tui.app import TravelApp
from tui_interaction import toggle_language


def fingerprint(path):
    info = path.stat()
    return info.st_ino, info.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "command",
    [
        ["check"],
        ["report"],
        ["profiles"],
        ["suggest", "--samples", "1"],
        ["--dry-run", "--scenario", "work", "--yes"],
    ],
)
def test_non_configuration_commands_do_not_rewrite_config(command, tmp_paths, darwin_platform, fake_runner):
    path = tmp_paths / "config.toml"
    path.write_text('# canary\n[options]\nlanguage="auto"\nscenario="away"\n')
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult("", "", 0)
    before = fingerprint(path)
    assert cli.main(command) == 0
    assert fingerprint(path) == before


async def test_tui_startup_refresh_and_explicit_language_write_are_distinct(tmp_paths):
    path = tmp_paths / "config.toml"
    path.write_text('# canary\n[options]\nlanguage="auto"\nscenario="away"\n')
    before = fingerprint(path)
    original = tomlkit.parse(path.read_text()).unwrap()
    app = TravelApp()
    async with app.run_test() as pilot:
        await app.wait_for_idle()
        await pilot.press("r")
        await app.wait_for_idle()
        assert fingerprint(path) == before
        await toggle_language(pilot)
        await app.wait_for_idle()
    changed = tomlkit.parse(path.read_text()).unwrap()
    assert fingerprint(path)[0] != before[0]  # atomic replacement
    assert changed["options"].pop("language") in {"en", "zh"}
    original["options"].pop("language")
    assert changed == original
    assert "# canary" in path.read_text()


def test_dry_run_tui_language_write_replaces_only_the_isolated_copy(tmp_paths):
    path = tmp_paths / "config.toml"
    path.write_text('# canary\n[options]\nlanguage="auto"\nscenario="away"\n')
    path.chmod(0o444)
    before = fingerprint(path)
    observed = {}

    async def interact():
        from travel_mode.paths import config_path

        copy = config_path()
        assert copy != path
        app = TravelApp(dry_run=True)
        async with app.run_test() as pilot:
            await app.wait_for_idle()
            await toggle_language(pilot)
            await app.wait_for_idle()
        observed["copy"] = copy

    command_lifecycle(["--dry-run"], lambda argv: asyncio.run(interact()))
    assert fingerprint(path) == before
    assert tomlkit.parse(observed["copy"].read_text())["options"]["language"] in {"en", "zh"}
    assert Path.home() == tmp_paths


def test_e2e_environment_tui_language_write_stays_in_fixture(tmp_paths, monkeypatch, fake_runner):

    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    from e2e_local import product_environment

    outside = tmp_paths / "config.toml"
    outside.write_text('[options]\nlanguage="auto"\n')
    before = fingerprint(outside)
    root = tmp_paths / "e2e"
    root.mkdir()
    target = root / "config.toml"
    target.write_text('[options]\nlanguage="auto"\nscenario="away"\n')

    async def interact():
        async with TravelApp().run_test() as pilot:
            await pilot.app.wait_for_idle()
            await toggle_language(pilot)
            await pilot.app.wait_for_idle()

    with product_environment(root, fake_runner):
        asyncio.run(interact())
    assert fingerprint(outside) == before
    assert tomlkit.parse(target.read_text())["options"]["language"] in {"en", "zh"}

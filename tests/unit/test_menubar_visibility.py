"""Visibility is a validated config option, not SwiftBar's persistent enable state."""

import json
from pathlib import Path

import pytest
from jsonschema import validate

from travel_mode import cli, i18n, menubar, state
from travel_mode.config import ConfigError, load_config, write_menubar
from travel_mode.paths import config_path
from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize("show", [None, "always", "session"])
def test_missing_or_valid_visibility_uses_explicit_default(tmp_path, show):
    path = tmp_path / "config.toml"
    assert load_config(path).menubar.show == "always"
    path.write_text("" if show is None else f'[menubar]\nshow = "{show}"\n')
    assert load_config(path).menubar.show == (show or "always")


@pytest.mark.parametrize("value", ['"invalid"', '""', '"SESSION"', "1", "true", "[]", "{}"])
def test_invalid_visibility_reports_original_file_and_key(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[menubar]\nshow = {value}\n")
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert error.value.path == str(path) and error.value.key == "menubar.show"


@pytest.mark.parametrize(
    "source,key", [('menubar = "session"', "menubar"), ("[menubar]\nunknown = true", "menubar.unknown")]
)
def test_menubar_table_and_keys_are_strict(tmp_path, source, key):
    path = tmp_path / "config.toml"
    path.write_text(source)
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert error.value.path == str(path) and error.value.key == key


@pytest.mark.parametrize("show", ["always", "session"])
@pytest.mark.parametrize("existing", [False, True])
def test_cli_visibility_preserves_comments_and_unrelated_settings(show, existing, darwin_platform, capsys):
    path = config_path()
    if existing:
        path.write_text(
            '# heading\n[options]\nlanguage = "en" # keep\n[menubar] # bar\nshow = "always" # choice\n'
        )
    assert cli.main(["config", "set-menubar", show]) == 0
    assert load_config(path).menubar.show == show
    if existing:
        assert (
            path.read_text()
            == f'# heading\n[options]\nlanguage = "en" # keep\n[menubar] # bar\nshow = "{show}" # choice\n'
        )
    else:
        assert load_config(path).apps.policy == "adaptive"
    assert str(path) in capsys.readouterr().out


@pytest.mark.parametrize("value", [None, "invalid", "SESSION"])
def test_invalid_cli_mode_does_not_write(value, darwin_platform, capsys):
    path = config_path()
    original = '# keep\n[menubar]\nshow = "always"\n'
    path.write_text(original)
    assert cli.main(["config", "set-menubar"] + ([] if value is None else [value])) == 2
    assert str(path) + ": menubar.show:" in capsys.readouterr().err
    assert path.read_text() == original


@pytest.mark.parametrize("existing", [False, True])
def test_visibility_dry_run_writes_only_isolated_copy(existing, darwin_platform, capsys):
    path = config_path()
    original = '# keep\n[menubar]\nshow = "always" # mode\n'
    if existing:
        path.write_text(original)
    assert cli.main(["config", "set-menubar", "session", "--dry-run", "--lang", "en"]) == 0
    saved = Path(capsys.readouterr().out.strip().rsplit(": ", 1)[1])
    assert saved != path and load_config(saved).menubar.show == "session"
    assert path.read_text() == original if existing else not path.exists()


def test_failed_atomic_visibility_write_keeps_original_and_does_not_refresh(
    tmp_path, monkeypatch, fake_runner
):
    path = tmp_path / "config.toml"
    original = '# keep\n[menubar]\nshow = "always"\n'
    path.write_text(original)

    def fail(self, target):
        raise OSError("fixture replace failure")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(ConfigError) as error:
        write_menubar("session", path)
    assert error.value.path == str(path) and error.value.key == "menubar.show"
    assert isinstance(error.value.__cause__, OSError)
    assert path.read_text() == original and list(tmp_path.iterdir()) == [path]
    assert tuple(menubar.REFRESH) not in fake_runner.calls


@pytest.mark.parametrize("show", ["always", "session"])
@pytest.mark.parametrize("active", [False, True])
def test_status_visibility_json_and_plain_output(show, active, darwin_platform, capsys):
    config_path().write_text(f'[menubar]\nshow = "{show}"\n')
    if active:
        snapshot = state.new_state()
        snapshot["scenario"] = "away"
        state.save_state(snapshot)
    assert cli.main(["status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    validate(result, json.loads(Path("docs/json-schema/status.schema.json").read_text()))
    hidden = show == "session" and not active
    assert result["menubar_show"] == show and result["menubar_hidden"] is hidden
    assert (result["swiftbar"] == "") is hidden
    assert cli.main(["status"]) == 0
    output = capsys.readouterr().out
    assert (i18n.t("menubar.hidden") in output) is hidden


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize(
    "show,active", [("always", False), ("always", True), ("session", False), ("session", True)]
)
def test_menubar_status_explains_mode_and_hidden_state(
    language, show, active, tmp_path, fake_runner, darwin_platform, monkeypatch, capsys
):
    monkeypatch.setenv("TRAVEL_MODE_LANG", language)
    config_path().write_text(f'[menubar]\nshow = "{show}"\n')
    folder = tmp_path / "plugins"
    folder.mkdir()
    fake_runner.responses[tuple(menubar.PREFERENCE)] = CmdResult(str(folder), "", 0)
    if active:
        state.save_state(state.new_state())
    hidden = show == "session" and not active
    assert cli.main(["menubar", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["show"] == show and result["hidden"] is hidden
    assert cli.main(["menubar", "status"]) == 0
    text = capsys.readouterr().out
    assert i18n.t("menubar.mode_status", mode=i18n.t("menubar.mode_" + show)) in text
    assert (i18n.t("menubar.hidden") in text) is hidden

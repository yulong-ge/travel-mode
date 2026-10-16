"""Menu refresh is one bounded source query, never a full audit."""

import json
from pathlib import Path

import pytest
from jsonschema import validate

from travel_mode import cli, i18n, state, status
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize(
    "active,language,source",
    [(False, "en", "AC Power"), (True, "zh", "Battery Power"), (True, "en", "UPS Power")],
)
def test_status_json_schema_only_source_command_and_no_migration(
    active, language, source, darwin_platform, fake_runner, monkeypatch, capsys
):
    if active:
        snapshot = state.new_state()
        snapshot["scenario"] = "work"
        state.save_state(snapshot)
    monkeypatch.setenv("TRAVEL_MODE_LANG", language)
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult(source, "", 0)
    monkeypatch.setattr(cli, "migrate_legacy", lambda: pytest.fail("status must not migrate"))
    monkeypatch.setattr(
        "travel_mode.system.platform.detect_capabilities", lambda: pytest.fail("capability scan")
    )
    observed = []

    def runner(cmd, timeout=None, interactive=False):
        observed.append((cmd, timeout, interactive))
        return fake_runner(cmd, timeout, interactive)

    shell.set_runner(runner)
    assert cli.main(["status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    validate(result, json.loads(Path("docs/json-schema/status.schema.json").read_text()))
    assert result["active"] is active and result["language"] == language
    assert observed == [(["pmset", "-g", "ps"], 2, False)]
    assert "--scenario" in result["swiftbar"] and 'param1="off"' in result["swiftbar"]
    assert "terminal=false" not in result["swiftbar"]


def test_status_follows_configured_language_without_writing(tmp_path, monkeypatch, darwin_platform, capsys):
    monkeypatch.delenv("TRAVEL_MODE_LANG", raising=False)
    path = tmp_path / "config.toml"
    original = '[options]\nlanguage="zh"\n'
    path.write_text(original)
    assert cli.main(["status", "--json"]) == 0
    assert "进入断电工作" in json.loads(capsys.readouterr().out)["swiftbar"]
    assert path.read_text() == original


def test_source_timeout_is_explicit_and_does_not_scan(darwin_platform, fake_runner, capsys):
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult("", "timeout", 124)
    assert cli.main(["status", "--json"]) == 1
    assert "timeout" in json.loads(capsys.readouterr().out)["error"]["message"]
    assert fake_runner.calls == [("pmset", "-g", "ps")]


def test_swiftbar_script_is_five_minutes_and_parses_single_status_json():
    path = Path("integrations/swiftbar/travel-mode.5m.sh")
    text = path.read_text()
    assert "status --json" in text and "plutil -extract swiftbar raw -o - -" in text
    assert "check --json" not in text and "while " not in text
    i18n.set_language("en")
    fixture = dict(active=True, scenario="work", started_at="2026-10-16 12:00:00", power_source="battery")
    payload = json.dumps({"swiftbar": status.render(fixture)}, ensure_ascii=False)
    menu = json.loads(payload)["swiftbar"]
    assert "Active: work" in menu and "Battery" in menu
    assert all(
        f'param1="{command}"' in menu for command in ("off", "check", "power", "history", "automation")
    )


def test_swiftbar_control_characters_cannot_inject_commands(monkeypatch):
    monkeypatch.setenv("TRAVEL_MODE_BIN", "travel-mode\nInjected | bash=evil")
    with pytest.raises(status.StatusError):
        status.render(dict(active=False, scenario=None, started_at=None, power_source="ac"))

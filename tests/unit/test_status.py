"""Menu refresh is one bounded source query, never a full audit."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import validate

from travel_mode import cli, i18n, state, status
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize(
    "active,language,source",
    [
        (False, "en", "AC Power"),
        (False, "zh", "Battery Power"),
        (True, "zh", "Battery Power"),
        (True, "en", "AC Power"),
        (True, "en", "UPS Power"),
    ],
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
    assert observed == [(["pmset", "-g", "ps"], 2, False), (["pmset", "-g"], 2, False)]
    assert result["sleep_ready"]["state"] == "blocked"
    assert result["sleep_warning"] is (active or source == "Battery Power")
    assert ("--scenario" in result["swiftbar"]) is not active
    assert ('param1="off"' in result["swiftbar"]) is active
    assert "terminal=false" not in result["swiftbar"]


def test_status_follows_configured_language_without_writing(tmp_path, monkeypatch, darwin_platform, capsys):
    monkeypatch.delenv("TRAVEL_MODE_LANG", raising=False)
    path = tmp_path / "config.toml"
    original = '[options]\nlanguage="zh"\n'
    path.write_text(original)
    assert cli.main(["status", "--json"]) == 0
    assert i18n.t("menubar.work") in json.loads(capsys.readouterr().out)["swiftbar"]
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
    assert "On the go" in menu and "Battery" in menu
    assert all(
        f'param1="{command}"' in menu for command in ("off", "check", "power", "history", "automation")
    )


def test_swiftbar_control_characters_cannot_inject_commands(monkeypatch):
    monkeypatch.setenv("TRAVEL_MODE_BIN", "travel-mode\nInjected | bash=evil")
    with pytest.raises(status.StatusError):
        status.render(dict(active=False, scenario=None, started_at=None, power_source="ac"))


@pytest.mark.parametrize("language", ["en", "zh"])
def test_blocked_menu_explains_reason_in_two_lines_and_offers_terminal_repair(language, monkeypatch):
    from travel_mode.sleep_readiness import evaluate

    i18n.set_language(language)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    readiness = evaluate("1")
    readiness["observed_at"] = "2026-10-22T10:20:00+00:00"
    readiness["assertions_observed_at"] = "2026-10-22T09:00:00+00:00"
    menu = status.render(
        dict(active=False, scenario=None, power_source="battery", sleep_ready=readiness),
        now=datetime(2026, 10, 22, 10, 30, tzinfo=timezone.utc),
    )
    lines = menu.splitlines()
    assert lines[0] == ":exclamationmark.triangle: | symbolize=true sfsize=16 dropdown=false emojize=false"
    assert t_line(language, "conclusion") in lines[2:]
    assert t_line(language, "detail") + " | color=gray" in lines[2:]
    assert 'param1="sleep" param2="repair" terminal=true' in menu
    # The old assertions cache supports no displayed reason, so it must not date this warning.
    assert [line for line in lines if line.startswith(("检查于", "Checked at"))] == [
        "检查于 18:20" if language == "zh" else "Checked at 18:20"
    ]
    assert "+00:00" not in menu
    assert "sfimage=" not in lines[0]


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize(
    "active,source", [(False, "ac"), (False, "battery"), (True, "ac"), (True, "battery")]
)
def test_sleep_warning_scope_and_quiet_power_menu(language, active, source):
    from travel_mode.sleep_readiness import evaluate

    i18n.set_language(language)
    readiness = evaluate("1")
    result = dict(
        active=active, scenario="work" if active else None, power_source=source, sleep_ready=readiness
    )
    menu = status.render(result)
    warning = active or source == "battery"
    assert status.sleep_warning(result) is warning
    assert menu.splitlines()[0] == status.header("exclamationmark.triangle" if warning else "leaf")
    assert ('param1="sleep" param2="repair"' in menu) is warning
    if not warning:
        section = menu.split("---")[1].strip().splitlines()
        assert section == [
            i18n.t("menubar.inactive"),
            i18n.t("menubar.source_ac"),
            i18n.t("menubar.sleep_on_power") + " | color=gray",
        ]
        assert t_line(language, "conclusion") not in menu
    else:
        assert t_line(language, "conclusion") in menu
    # Quiet menu presentation must not change the finding used by check and plan.
    assert readiness["state"] == "blocked" and not readiness["ready"]
    result["sleep_ready"] = evaluate("0")
    assert status.sleep_warning(result) is False
    assert "exclamationmark.triangle" not in status.render(result).splitlines()[0]


def t_line(language, kind):
    return {
        "zh": {
            "conclusion": "合盖后可能不会睡眠：系统睡眠已被禁用",
            "detail": "可能由远程控制或防睡工具的「接电时防止睡眠」选项引起",
        },
        "en": {
            "conclusion": "May not sleep when the lid closes: system sleep is disabled",
            "detail": "A remote-access or keep-awake tool’s “prevent sleep on power” option may cause this",
        },
    }[language][kind]


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize(
    "observed,expected,stale",
    [
        ("2026-10-22T10:16:00+00:00", ("18:16", "18:16"), False),
        ("2026-10-22T09:30:00+00:00", ("17:30", "17:30"), False),
        ("2026-10-22T09:29:59+00:00", ("17:29", "17:29"), True),
        ("2026-10-21T03:45:04+00:00", ("10 月 21 日 11:45", "10/21, 11:45"), True),
        # UTC yesterday is still today for the user's local calendar.
        ("2026-10-21T23:45:00+00:00", ("07:45", "07:45"), True),
    ],
)
def test_menu_dates_oldest_visible_reason_in_local_time(language, observed, expected, stale, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    i18n.set_language(language)
    readiness = dict(
        observed_at="2026-10-22T10:20:00+00:00",
        items=[
            dict(level="red", message="Current reason"),
            dict(level="red", message="Cached reason", observed_at=observed),
        ],
    )
    line = status.checked_line(readiness, now=datetime(2026, 10, 22, 10, 30, tzinfo=timezone.utc))
    assert line == (
        "检查于 " + expected[0] + ("（可能已过时，点「体检」重新检查）" if stale else "")
        if language == "zh"
        else "Checked at "
        + expected[1]
        + (" (may be outdated; click “Health check” to check again)" if stale else "")
    )


def test_menu_converts_historical_time_with_its_own_local_dst_offset(monkeypatch):
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    i18n.set_language("en")
    line = status.checked_line(
        dict(observed_at="2026-07-21T10:45:00+00:00"),
        now=datetime(2026, 12, 22, 10, 30, tzinfo=timezone.utc),
    )
    assert line.startswith("Checked at 7/21, 03:45")


@pytest.mark.parametrize("text,expected", [("", ":leaf.fill:"), ("1:20", ":leaf.fill: 1:20")])
def test_embedded_symbol_title_has_no_leading_blank_or_separate_image(text, expected):
    assert (
        status.header("leaf.fill", text)
        == expected + " | symbolize=true sfsize=16 dropdown=false emojize=false"
    )

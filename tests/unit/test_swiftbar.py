"""Hermetic Bash protocol tests: only fixture executables, never host observations."""

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from travel_mode import i18n, menubar, status
from travel_mode.system.shell import CmdResult

# Retain only the interpreter boundary for this self-contained shell protocol.
# No product/system subprocess is allowed: every child command is a local fixture.
_POPEN = subprocess.Popen
FIXTURES = Path(__file__).parents[1] / "fixtures" / "swiftbar"


def model(case):
    active = case in {"work", "away", "blocked-active-ac", "blocked-active-battery"}
    ac = case in {"charging", "charged", "paused", "finishing", "blocked-ac", "blocked-active-ac"}
    percent = 100 if case in {"charged", "finishing"} else 85 if ac else 82
    states = {"paused": "not charging", "finishing": "finishing charge"}
    result = dict(
        active=active,
        scenario=("work" if case.startswith("blocked-") else case) if active else None,
        started_at="2026-10-17T10:00:00+00:00" if active else None,
        duration_minutes=80 if active else None,
        power_source="ac" if ac else "battery",
        battery=dict(
            percent=percent,
            remaining_minutes=190 if active else None,
            average_watts=9.8 if active else None,
            estimate_source="session_telemetry" if active else None,
            charging_state=states.get(case, case if ac else "discharging"),
        ),
        app_version="1.1.0",
    )
    if case.startswith("blocked-"):
        from travel_mode.sleep_readiness import evaluate

        readiness = evaluate("1")
        readiness["observed_at"] = "2026-10-22T10:16:52+00:00"
        if case == "blocked-stale":
            cached = evaluate("0", [dict(process="Example", pid="123", scope="system")])["items"][0]
            cached["observed_at"] = "2026-10-21T03:45:04+00:00"
            readiness["items"].append(cached)
        result["sleep_ready"] = readiness
    return result


def bash_run(script, env, *, syntax=False):
    # Absolute Bash interpreter; the script's external reads are fixture-only.
    args = ["/bin/bash", "-n", str(script)] if syntax else ["/bin/bash", str(script)]
    with _POPEN(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        stdout, stderr = child.communicate(timeout=10)
    return child.returncode, stdout, stderr


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize(
    "case",
    [
        "idle",
        "work",
        "away",
        "charging",
        "charged",
        "paused",
        "finishing",
        "unknown",
        "blocked-fresh",
        "blocked-stale",
        "blocked-ac",
        "blocked-active-ac",
        "blocked-active-battery",
    ],
)
def test_complete_localized_menu_fixture(case, language, monkeypatch):
    i18n.set_language(language)
    monkeypatch.setenv("TRAVEL_MODE_BIN", "/fixture/travel-mode")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    assert (
        status.render(model(case), now=datetime(2026, 10, 22, 10, 30, tzinfo=timezone.utc)) + "\n"
        == (FIXTURES / f"{case}-{language}.txt").read_text()
    )
    if not model(case)["active"]:
        assert status.render(model(case)).splitlines()[2] == (
            "省电模式未开启" if language == "zh" else "Power saving is off"
        )


@pytest.mark.parametrize(
    "case",
    [
        "always-idle",
        "always-work",
        "session-idle",
        "session-work",
        "error",
        "empty-error",
        "invalid",
        "missing",
    ],
)
@pytest.mark.parametrize("language", ["en", "zh"])
def test_script_output_and_single_status_call(case, language, tmp_path):
    script = tmp_path / "plugin.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "trace"
    menu = (FIXTURES / ("idle-" + language + ".txt")).read_text()
    visible_case = case.startswith(("always-", "session-"))
    if visible_case:
        i18n.set_language(language)
        show, scenario = case.split("-", 1)
        menu = status.render({**model(scenario), "menubar_show": show})
        menu += "\n" if menu else ""
    if case == "error":
        i18n.set_language(language)
        menu = status.error_menu("fixture timeout") + "\n"
    if case == "empty-error":
        menu = ""
    output = tmp_path / "menu.txt"
    output.write_text(menu)
    payload = tmp_path / "status.json"
    payload.write_text(json.dumps({"swiftbar": menu.rstrip("\n")}) if case != "invalid" else "not json")
    tool = bin_dir / "travel-mode"
    tool.write_text(
        '#!/bin/bash\n[[ "$*" == "status --json" ]] || exit 90\n'
        'printf "%s\\n" "$*" >> "$TRACE"\n/bin/cat "$PAYLOAD"\n'
        + ("exit 1\n" if case in {"error", "empty-error"} else "")
    )
    tool.chmod(0o755)
    parser = bin_dir / "plutil"
    # Check the exact extraction contract and exact JSON input; the renderer
    # schema/JSON is independently tested with jsonschema and json.loads.
    parser.write_text(
        '#!/bin/bash\n[[ "$*" == "-extract swiftbar raw -o - -" ]] || exit 91\n'
        'IFS= read -r input\n[[ "$input" == "$(< "$PAYLOAD")" ]] || exit 92\n'
        '[[ "$input" != "not json" ]] || exit 1\n/bin/cat "$MENU"\n'
    )
    parser.chmod(0o755)
    text = menubar.payload().decode().replace("/usr/bin/plutil", str(parser))
    # Prevent discovery of any globally installed product on the test host.
    text = text.replace("/opt/homebrew/bin/travel-mode", str(tmp_path / "homebrew/travel-mode"))
    text = text.replace("/usr/local/bin/travel-mode", str(tmp_path / "local/travel-mode"))
    script.write_text(text)
    env = dict(
        HOME=str(tmp_path),
        PATH=str(bin_dir),
        LANG="C",
        TRAVEL_MODE_LANG=language,
        TRAVEL_MODE_BIN=str(tool),
        TRACE=str(trace),
        PAYLOAD=str(payload),
        MENU=str(output),
    )
    if case == "missing":
        tool.unlink()
    rc, stdout, stderr = bash_run(script, env)
    assert not stderr
    if visible_case or case == "error":
        assert rc == 0 and stdout == menu
    else:
        fixture = "invalid" if case == "empty-error" else case
        assert stdout == (FIXTURES / f"{fixture}-{language}.txt").read_text()
        assert rc == (1 if case in {"invalid", "empty-error"} else 0)
    assert (trace.read_text() if trace.exists() else "") == ("" if case == "missing" else "status --json\n")


@pytest.mark.parametrize("show,target,checked", [("always", "session", False), ("session", "always", True)])
def test_menu_setting_toggles_config_without_disabling_swiftbar_plugin(show, target, checked):
    line = next(
        line
        for line in status.render({**model("work"), "menubar_show": show}).splitlines()
        if 'param2="set-menubar"' in line
    )
    assert f'param1="config" param2="set-menubar" param3="{target}" terminal=true refresh=true' in line
    assert ("checked=true" in line) is checked
    assert not any(word in line for word in ("enableplugin", "disableplugin", "toggleplugin"))


def test_script_syntax_and_metadata():
    path = Path("integrations/swiftbar/travel-mode.5m.sh").absolute()
    assert bash_run(path, {"PATH": "/nonexistent"}, syntax=True) == (0, "", "")
    text = path.read_text()
    # Bootstrap messages cannot invoke the missing tool; keep their tiny embedded
    # copies identical to the same keys in both product language catalogs.
    for language in ("en", "zh"):
        for key in ("bootstrap_missing", "bootstrap_install", "bootstrap_failed"):
            assert i18n.catalog(language)["menubar." + key] in text
    for key in ("hideAbout", "hideRunInTerminal", "hideLastUpdated", "hideDisablePlugin", "hideSwiftBar"):
        assert f"<swiftbar.{key}>true</swiftbar.{key}>" in text
    assert "refreshOnOpen" not in "\n".join(line for line in text.splitlines() if line.startswith("# <"))
    assert "check --json" not in text and "while " not in text


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1) 82%; discharging; 3:10 remaining",
            ("battery", {"percent": 82, "remaining_minutes": 190, "charging_state": "discharging"}),
        ),
        (
            "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1) 99%; charging; (no estimate)",
            ("ac", {"percent": 99, "remaining_minutes": None, "charging_state": "charging"}),
        ),
        (
            "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1) 100%; charged; 0:00 remaining",
            ("ac", {"percent": 100, "remaining_minutes": 0, "charging_state": "charged"}),
        ),
        (
            "Now drawing from 'AC Power'",
            ("ac", {"percent": None, "remaining_minutes": None, "charging_state": None}),
        ),
    ],
)
def test_single_pmset_observation(raw, expected, fake_runner):
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult(raw, "", 0)
    assert status.observation() == expected
    assert fake_runner.calls == [("pmset", "-g", "ps")]


@pytest.mark.parametrize("line", ["no percent", "101%; discharging;", "80%; discharging; 1:70 remaining"])
def test_invalid_battery_details_fail(line, fake_runner):
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult(
        "Battery Power\n -InternalBattery-0 " + line, "", 0
    )
    with pytest.raises(status.StatusError):
        status.observation()


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize(
    "case,en,zh",
    [
        ("finishing-100", "On power · Fully charged", "接电 · 已充满"),
        ("charged-99", "On power · Fully charged", "接电 · 已充满"),
        ("finishing-99", "On power · Fully charged", "接电 · 已充满"),
        ("charging-100", "On power · Fully charged", "接电 · 已充满"),
        ("paused-100", "On power · Fully charged", "接电 · 已充满"),
        ("charging-85", "Charging · 85%", "充电中 · 85%"),
        ("charging-99", "Charging · 99%", "充电中 · 99%"),
        ("paused-85", "On power · 85% (not charging)", "接电 · 85%（未充电）"),
        ("charged-98", "On power · 98% (not charging)", "接电 · 98%（未充电）"),
        ("finishing-98", "Charging · 98%", "充电中 · 98%"),
        ("battery-82", "Battery 82%", "电池 82%"),
        ("battery-unknown", "Battery 82%", "电池 82%"),
        ("ac-no-battery", "On power", "接电"),
    ],
)
def test_pmset_power_line_language_and_charge_precedence(case, en, zh, language, fake_runner):
    # finishing-100 is the current host recording with its battery id removed.
    # The other rows use pmset's same output grammar with controlled states;
    # they are fixture scenarios, not claims of additional host recordings.
    raw = (FIXTURES.parent / "pmset-ps" / (case + ".txt")).read_text()
    fake_runner.responses[("pmset", "-g", "ps")] = CmdResult(raw, "", 0)
    i18n.set_language(language)
    source, battery = status.observation()
    result = {**model("idle"), "power_source": source, "battery": battery}
    assert status.render(result).splitlines()[3] == (en if language == "en" else zh)
    assert fake_runner.calls == [("pmset", "-g", "ps")]
    # Display normalization must not change the source data exposed as JSON.
    assert battery["charging_state"] == (None if case == "ac-no-battery" else raw.split(";")[1].strip())


def test_duration_uses_refresh_time_and_preserves_start(monkeypatch):
    snapshot = {"scenario": "work", "session": {"started_at": "2026-10-17T10:00:00+00:00"}}
    monkeypatch.setattr(status, "load_state", lambda: snapshot)
    result = status.data(now=datetime(2026, 10, 17, 11, 20, 59, tzinfo=timezone.utc))
    assert result["duration_minutes"] == 80 and result["started_at"] == snapshot["session"]["started_at"]


def test_discovery_precedence_and_limited_path(tmp_path):
    script = tmp_path / "discover.sh"
    text = menubar.payload().decode().split("if ! TRAVEL_MODE_BIN=")[0]
    text = text.replace("/opt/homebrew/bin/travel-mode", str(tmp_path / "brew"))
    text = text.replace("/usr/local/bin/travel-mode", str(tmp_path / "local"))
    script.write_text(text + "\nresolve_binary\n")
    home = tmp_path / ".local/bin/travel-mode"
    home.parent.mkdir(parents=True)
    candidates = [
        tmp_path / "override",
        home,
        tmp_path / "brew",
        tmp_path / "local",
        tmp_path / "path/travel-mode",
    ]
    for item in candidates:
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_text("#!/bin/bash\nexit 0\n")
        item.chmod(0o755)
    env = dict(HOME=str(tmp_path), PATH=str(candidates[-1].parent), TRAVEL_MODE_BIN=str(candidates[0]))
    for item in candidates:
        assert bash_run(script, env) == (0, str(item) + "\n", "")
        item.unlink()

"""Behavior contracts for the task-based interface and scenario safety."""

from dataclasses import replace

import pytest

from travel_mode.app_policy import recommend
from travel_mode.config import Config
from travel_mode.profiles import builtin_profiles, profile_scope
from travel_mode.system import pmset
from travel_mode.tui import view_models


def test_work_prepares_sleep_without_shortening_active_work(fake_runner):
    config = Config.defaults()
    with profile_scope(config, None, scenario="work"):
        targets = {key: value for key, _, value in pmset.power_recommendations()}
    assert targets["powernap"] == "0" and targets["womp"] == "0"
    assert "sleep" not in targets and "displaysleep" not in targets and "hibernatemode" not in targets


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_network_keepalive_cannot_be_disabled_by_profile_or_override(scenario, fake_runner):
    config = replace(Config.defaults(), power_overrides={"tcpkeepalive": "0"})
    with profile_scope(config, None, scenario=scenario):
        assert all(value == "1" for key, _, value in pmset.power_recommendations() if key == "tcpkeepalive")


def test_away_recommendations_quit_ordinary_apps_but_keep_interactive_opt_in():
    assert recommend("Ordinary Demo")["away"] == "quit"
    assert recommend("Google Chrome")["away"] == "ask"


def test_flight_keeps_radios_until_user_selects_them():
    flight = builtin_profiles()["flight"].values
    assert flight["wifi"] == "keep" and flight["bluetooth"] == "keep"


@pytest.mark.parametrize(
    "state,app_running,expected",
    [
        ("planned", True, ["select", "cycle"]),
        ("running", True, []),
        ("done", False, []),
        ("warning", False, []),
        ("failed", True, ["retry", "force"]),
        ("failed", False, []),
        ("skipped", True, []),
    ],
)
def test_plan_row_state_controls_are_unambiguous(state, app_running, expected):
    assert view_models.plan_controls(state, group="apps", app_running=app_running) == expected


def test_helper_cleanup_warning_does_not_claim_force_quit():
    from travel_mode.i18n import t

    row = view_models.plan_item(
        "helper-warning", "apps", "Demo", "quit", "", dict(kind="app", path="/Demo.app")
    )
    row.update(state="warning", warning_key="failure.quit_helpers", app_running=False)
    view = view_models.plan_view(row)
    assert view["action"] == t("app_view.exited")
    assert not view["operations"]


@pytest.mark.parametrize(
    "sd,scope,expected",
    [
        ("0", None, "ready"),
        ("1", None, "blocked"),
        ("0", "idle", "idle"),
        ("0", "system", "blocked"),
        (None, None, "unknown"),
    ],
)
def test_sleep_readiness_keeps_idle_assertions_out_of_red(sd, scope, expected):
    from travel_mode.sleep_readiness import evaluate

    holders = [] if scope is None else [dict(pid="42", process="Remote Demo", scope=scope)]
    result = evaluate(sd, holders)
    assert result["state"] == expected
    assert result["ready"] is (expected in {"ready", "idle"})
    if scope == "idle":
        assert result["items"][0]["level"] == "amber"
        assert "合盖仍会睡眠" in result["items"][0]["message"]


def test_sleep_switch_sources_require_a_public_behavior_rule():
    from types import SimpleNamespace

    from travel_mode.sleep_readiness import evaluate

    helper = SimpleNamespace(command="/Demo/AmphetamineNet.app/Contents/MacOS/AmphetamineNet")
    blocked = evaluate("1", processes=[helper])
    assert "可能来自 AmphetamineNet" in blocked["items"][0]["message"]
    assert "可能再次改回" in blocked["items"][0]["message"]
    unknown = evaluate("1", processes=[SimpleNamespace(command="/Demo/UURemoteHelper")])
    assert "UURemote" not in unknown["items"][0]["message"]
    assert "某个程序可能修改了这个设置" in unknown["items"][0]["message"]
    assert "AmphetamineNet" not in evaluate("0", processes=[helper])["items"][0]["message"]


def test_empty_plist_is_not_noise_but_malformed_plist_is(tmp_path, fake_runner):
    import plistlib

    from travel_mode.system.launchd import svc_scan

    folder = tmp_path / "LaunchAgents"
    folder.mkdir()
    (folder / "placeholder.plist").write_bytes(plistlib.dumps({}))
    (folder / "broken.plist").write_text("not a plist")
    result = svc_scan(Config.defaults(), directories=[folder])
    assert len(result["invalid"]) == 1
    assert result["invalid"][0]["plist_path"].endswith("broken.plist")


def test_menu_readiness_only_uses_one_global_read_and_dated_cached_assertions(fake_runner):
    from travel_mode import status
    from travel_mode.system.shell import CmdResult

    fake_runner.responses[("pmset", "-g")] = CmdResult("SleepDisabled 1\n", "", 0)
    data = status.data()
    assert data["sleep_ready"]["state"] == "blocked"
    assert data["swiftbar"].splitlines()[0].startswith(":exclamationmark.triangle: | symbolize=true")
    assert fake_runner.calls == [("pmset", "-g", "ps"), ("pmset", "-g")]


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_mutation_boundary_refuses_network_keepalive_off(scenario):
    from travel_mode.actions.power import SetPowerProfile

    with profile_scope(Config.defaults(), None, scenario=scenario), pytest.raises(ValueError):
        SetPowerProfile(["tcpkeepalive", "0"]).command()


def test_readiness_cache_atomic_private_bounded_and_strict(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from travel_mode import sleep_readiness as readiness
    from travel_mode.state import StateError

    monkeypatch.setenv("TRAVEL_MODE_STATE", str(tmp_path / "state.json"))
    assert readiness.cached() is None
    value = readiness.evaluate("0", [dict(pid="42", process="Demo", scope="system")])
    original_replace = Path.replace
    observed = []

    def replace(source, destination):
        if readiness.cache_path().exists():
            observed.append(readiness.cached())
        original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    readiness.store(value)
    readiness.store(readiness.evaluate("0"))
    assert observed == [value]
    assert readiness.cache_path().stat().st_mode & 0o777 == 0o600
    assert readiness.cached()["state"] == "ready"
    readiness.cache_path().write_text(json.dumps({"state": "ready"}))
    with pytest.raises(StateError, match="invalid fields"):
        readiness.cached()
    readiness.cache_path().write_bytes(b"x" * 262145)
    with pytest.raises(StateError, match="256 KiB"):
        readiness.cached()


def test_menu_unknown_without_assertions_and_explicitly_dated_cache():
    from travel_mode.sleep_readiness import evaluate, menu_observation

    assert menu_observation("SleepDisabled 0", None)["state"] == "unknown"
    cached = evaluate("0", [dict(pid="42", process="Demo", scope="system")])
    cached["observed_at"] = "2026-10-01T12:00:00+00:00"
    result = menu_observation("SleepDisabled 0", None, observation=cached)
    assert result["state"] == "blocked"
    assert result["assertions_observed_at"] == cached["observed_at"]
    assert result["items"][-1]["cached"] is True


def test_session_view_uses_whole_session_accumulator_and_never_charging_data():
    from datetime import datetime, timezone

    from travel_mode.tui.session_view import session_view

    state = dict(
        scenario="work",
        session=dict(
            started_at="2026-10-19T12:00:00+00:00",
            external_seen=False,
            start=dict(percent=80, telemetry=dict(system_count=10, system_sum_mw=100000)),
        ),
        batt_prof="lowpowermode 0 powernap 1",
        frozen_apps=["demo"],
        unloaded_svcs=["agent"],
    )
    reading = dict(
        available=True,
        external_connected=False,
        is_charging=False,
        remaining_mah=3000,
        maximum_mah=5000,
        remaining_wh=36,
        telemetry=dict(system_count=20, system_sum_mw=198000),
        power_source="PowerTelemetryData.SystemLoad",
    )
    now = datetime(2026, 10, 19, 14, tzinfo=timezone.utc)
    result = session_view(state, reading, now=now)
    assert result["percent_change"] == 20
    assert result["average_watts"] == pytest.approx(9.8)
    assert result["remaining_minutes"] == 220
    assert "2" in result["title"]
    state["session"]["external_seen"] = True
    assert session_view(state, reading, now=now)["average_watts"] is None
    assert session_view(state, None, now=now)["remaining_minutes"] is None


def test_power_plan_groups_each_domain_in_one_action_and_keeps_keepalive_on():
    from travel_mode.tui.plan import execution_units, item

    rows = [
        item(str(i), "power", key, "apply", "", dict(kind="power", domain=domain, values=[key, value]))
        for i, (domain, key, value) in enumerate(
            [
                ("-b", "lowpowermode", "1"),
                ("-b", "powernap", "0"),
                ("-c", "powernap", "0"),
                ("-c", "womp", "0"),
                ("-b", "tcpkeepalive", "1"),
            ]
        )
    ]
    units = list(execution_units(rows, "work", {}, lambda: None))
    assert len(units) == 2
    assert units[0][1].command() == ["pmset", "-b", "lowpowermode", "1", "powernap", "0", "tcpkeepalive", "1"]
    assert units[1][1].command() == ["pmset", "-c", "powernap", "0", "womp", "0"]


def test_json_check_reuses_health_scan_to_update_menu_observation(monkeypatch, fake_runner):
    from travel_mode import sleep_readiness, structured
    from travel_mode.flows import doctor
    from travel_mode.system.health import collect

    report = collect()
    monkeypatch.setattr(doctor, "health_collect", lambda: report)
    fake_runner.calls.clear()
    assert structured.collect is doctor.collect
    assert structured.collect() is report
    assert sleep_readiness.cached()["state"] == sleep_readiness.from_report(report)["state"]
    assert fake_runner.calls == []

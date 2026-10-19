"""Default health text stays concise; verbose and JSON retain observations."""

import json
import re

import pytest

from travel_mode import health_view
from travel_mode.flows import doctor
from travel_mode.i18n import set_language, t


def finding(key, *, evidence=(), actions=(), status="hit"):
    return dict(
        id=key,
        title=t(f"health.{key}.title"),
        status=status,
        evidence=list(evidence),
        actions=list(actions),
        impact=t(f"health.{key}.impact"),
        manual=t(f"health.{key}.manual"),
        error=None,
    )


def report(*rows):
    return dict(checks=list(rows), elapsed_seconds=0, budget_seconds=15, limitations=[])


@pytest.mark.parametrize("language", ["en", "zh"])
def test_assertion_observations_are_verbose_only(language):
    set_language(language)
    raw = report(
        finding(
            "assertions",
            evidence=['pid 42(Remote): 121:00:00 PreventUserIdleSystemSleep named: "remote"'],
        )
    )
    raw["assertion_context"] = dict(session_seconds=7200, power_transition=True)
    plain = list(doctor.lines(raw, scenario="away"))
    expected = "▲ " + t("health_view.idle_long", name="Remote", age=t("health_view.held_days", days=5))
    assert plain == [expected]
    verbose = "\n".join(doctor.lines(raw, verbose=True, scenario="away"))
    encoded = json.dumps(health_view.presentation(raw), ensure_ascii=False)
    for key in ("pre_session", "long_observation"):
        observation = t("health_view." + key)
        assert observation in verbose and observation in encoded


@pytest.mark.parametrize("language,expected", [("en", "Set to 50%"), ("zh", "调到 50%")])
def test_brightness_conclusion_and_one_percentage_action(language, expected):
    set_language(language)
    action = dict(kind="brightness", value=0.5)
    raw = report(finding("brightness", evidence=["brightness=0.87; threshold=0.7"], actions=[action]))
    lines = list(doctor.lines(raw, scenario="away"))
    assert lines == ["▲ " + t("health_view.brightness_high", value=87, threshold=70), " " + expected]
    assert doctor.action_label(action) == expected
    assert doctor.make_action(action, {}, lambda: None).value == 0.5


@pytest.mark.parametrize("language", ["en", "zh"])
def test_other_assertion_holders_and_actions_remain_in_verbose_and_json(language):
    set_language(language)
    raw = report(
        finding(
            "assertions",
            evidence=[
                f'pid {pid}({name}): PreventSystemSleep named: "task"'
                for pid, name in [(42, "First"), (43, "Second")]
            ],
            actions=[
                dict(kind="quit", name=name, path=f"/fixture/{name}.app") for name in ("First", "Second")
            ],
        )
    )
    plain = list(doctor.lines(raw, scenario="away"))
    assert len(plain) == 2 and "Second" not in "\n".join(plain)
    verbose = "\n".join(doctor.lines(raw, scenario="away", verbose=True))
    assert "Second" in verbose and doctor.action_label(raw["checks"][0]["actions"][1]) in verbose
    assert len(health_view.presentation(raw)[0]["actions"]) == 2


@pytest.mark.parametrize("language", ["en", "zh"])
def test_unavailable_capability_keys_only_appear_in_verbose(language):
    set_language(language)
    raw = report(finding("sleep_disabled", evidence=["pmset missing SleepDisabled"], status="unavailable"))
    plain = "\n".join(doctor.lines(raw, scenario="away"))
    assert "SleepDisabled" not in plain and "pmset" not in plain
    assert t("health_view.capability_unavailable") in plain
    assert "SleepDisabled" in "\n".join(doctor.lines(raw, verbose=True, scenario="away"))


@pytest.mark.parametrize("language", ["en", "zh"])
def test_unverified_caffeinate_has_one_manual_advice_sentence(language):
    set_language(language)
    raw = report(finding("keep_awake", evidence=["PID 42: /usr/bin/caffeinate; PPID 1: launchd"]))
    plain = list(doctor.lines(raw, scenario="away"))
    assert len(plain) == 1 and plain[0].endswith(t("health.advice.caffeinate"))
    assert "PID" not in "\n".join(plain)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_duplicate_process_reference_stays_in_the_single_conclusion(language):
    set_language(language)
    raw = report(
        finding("keep_awake", evidence=["PID 42: caffeinate"]),
        finding("assertions", evidence=['pid 42(caffeinate): PreventSystemSleep named: "task"']),
    )
    row = health_view.presentation(raw)[1]
    assert row["summary"] == "✓ " + t(
        "health_view.reference", title=t("health.assertions.title"), references=t("health.keep_awake.title")
    )
    assert not row["action_hint"] and not row["display_actions"]


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize(
    "key",
    [
        "sleep_disabled",
        "keep_awake",
        "assertions",
        "displays",
        "spotlight",
        "backup",
        "low_power",
        "brightness",
        "high_cpu",
    ],
)
@pytest.mark.parametrize("status", ["hit", "clear", "unavailable"])
def test_every_default_item_has_only_conclusion_and_one_action(language, key, status):
    set_language(language)
    row = finding(
        key, status=status, evidence=["brightness=0.87; threshold=0.7"] if key == "brightness" else []
    )
    if status == "unavailable":
        row["error"] = "invalid raw_value=0.5  PID 123"
    if key in {"keep_awake", "high_cpu"}:
        row["actions"] = [
            dict(kind="quit", name=name, path=f"/fixture/{name}.app") for name in ("First", "Second")
        ]
    lines = list(doctor.lines(report(row), scenario="away"))
    assert 1 <= len(lines) <= 2
    text = "\n".join(lines)
    assert "  " not in text
    assert not re.search(r"\b\d+\.\d+\b", text)
    for forbidden in (
        "brightness=",
        "threshold=",
        "lowpowermode=",
        "PID",
        "raw_value",
        "profile",
        "配置档",
        "体检配置",
    ):
        assert forbidden not in text
    view = health_view.presentation(report(row))[0]
    assert len(view["display_actions"]) <= 1
    assert view["actions"] == row["actions"]


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("key", ["displays", "backup", "keep_awake", "high_cpu", "brightness"])
def test_manual_advice_is_second_sentence_without_a_separate_label(language, key):
    set_language(language)
    evidence = ["brightness=0.87; threshold=0.7"] if key == "brightness" else []
    raw = report(finding(key, evidence=evidence))
    lines = list(doctor.lines(raw, scenario="away"))
    assert len(lines) == 1
    assert " · " not in lines[0]
    assert not lines[0].endswith(t("health.hint." + key))
    if key == "displays":
        expected = (
            "▲ 外接显示器已连接，会增加耗电。不用时可以断开。"
            if language == "zh"
            else "▲ External display connected; this increases energy use. Disconnect it when not needed."
        )
        assert lines[0] == expected

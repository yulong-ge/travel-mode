"""Complete localized output examples from the sealed read-only fixture."""

import json
import os
import re
from pathlib import Path

import pytest

from travel_mode import health_view, session_conflict, state, status
from travel_mode.flows import doctor
from travel_mode.i18n import set_language, t
from travel_mode.system import health, pmset
from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize("language", ["en", "zh"])
def test_real_use_output_examples(language, fake_runner, monkeypatch, tmp_path):
    set_language(language)
    monkeypatch.setenv("TRAVEL_MODE_BIN", "/fixture/travel-mode")
    fake_runner.responses[("pmset", "-g")] = CmdResult("SleepDisabled 0\nlowpowermode 1\n", "", 0)
    report = health.collect()
    assertions = next(r for r in report["checks"] if r["id"] == "assertions")
    assertions.update(
        status="hit",
        evidence=['pid 42(UURemote): [0x0001] 121:00:00 PreventUserIdleSystemSleep named: "remote"'],
    )
    report["assertion_context"] = dict(session_seconds=172800, power_transition=True, scenario="away")
    brightness = next(r for r in report["checks"] if r["id"] == "brightness")
    brightness.update(
        status="hit",
        evidence=["brightness=0.87; threshold=0.7"],
        actions=[dict(kind="brightness", value=0.5)],
    )
    report_view = health_view.presentation(report, scenario="away")
    assert next(r for r in report_view if r["id"] == "assertions")["advisory"]
    health_text = "\n".join(doctor.lines(report, scenario="away")) + "\n"
    assert "PID" not in health_text and "0x0001" not in health_text and "ps %CPU" not in health_text
    assert "  " not in health_text and not re.search(r"\b\d+\.\d+\b", health_text)
    for key in ("pre_session", "long_observation"):
        assert t("health_view." + key) not in health_text
    verbose_text = "\n".join(doctor.lines(report, scenario="away", verbose=True)) + "\n"
    preview = pmset.power_preview(
        {"b": {"lowpowermode": "1"}, "a": {"powernap": "1", "womp": "1"}}, scenario="work"
    )
    power_text = (
        "\n".join(
            [
                *(r["explanation"] for r in preview["ac_changes"]),
                t("preview.satisfied", names=", ".join(preview["satisfied"])),
            ]
        )
        + "\n"
    )
    old = state.new_state()
    old.update(
        scenario="away",
        session={"started_at": "2026-10-17T16:26:00+00:00"},
        quit_apps=["/fixture/A.app"] * 4,
        frozen_apps=["/fixture/B.app"] * 3,
        unloaded_svcs=["/fixture/demo.plist"] * 2,
    )
    conflict_text = (
        session_conflict.describe(old) + "\n" + t("conflict.prompt") + "\n" + t("conflict.yes_cancel") + "\n"
    )
    menu = (
        status.render(
            dict(
                active=True,
                scenario="work",
                duration_minutes=80,
                power_source="battery",
                battery=dict(
                    percent=62, remaining_minutes=220, average_watts=9.8, estimate_source="session_telemetry"
                ),
            )
        )
        + "\n"
    )
    assert "9.8 W" in menu
    destination = Path(os.environ.get("TM_EXAMPLES_OUTPUT", tmp_path))
    destination.mkdir(parents=True, exist_ok=True)
    for name, text in (
        ("health", health_text),
        ("health-verbose", verbose_text),
        ("power-preview", power_text),
        ("session-conflict", conflict_text),
        ("menubar", menu),
    ):
        (destination / f"{name}-{language}.txt").write_text(text)
    report["display_checks"] = report_view
    (destination / f"health-{language}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )

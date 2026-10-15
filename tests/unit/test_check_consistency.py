"""check output acceptance: configured services appear, power meaning appears once."""

from __future__ import annotations

import plistlib

import pytest

import travel_mode.flows.check as flow_check
from travel_mode.config import load_config
from travel_mode.i18n import t
from travel_mode.paths import config_path
from travel_mode.system.shell import CmdResult


@pytest.fixture(autouse=True)
def english_messages(monkeypatch):
    monkeypatch.setattr("travel_mode.i18n._language", "en")


def _configure(tmp_path) -> None:
    config_path().write_text(
        '[services]\nunload = ["demo-backup", "ghost-service"]\n',
        encoding="utf-8",
    )
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    with (agents / "demo-backup.plist").open("wb") as handle:
        plistlib.dump({"Label": "homebrew.mxcl.demo-backup"}, handle)


def test_check_lists_configured_services_and_single_power_explanation(fake_runner, tmp_path, capsys):
    _configure(tmp_path)
    fake_runner.responses[("launchctl", "list")] = CmdResult("101\t0\thomebrew.mxcl.demo-backup\n", "", 0)

    assert flow_check.mode_check() == 0
    output = capsys.readouterr().out

    # item 1: a short config name matches the loaded real label in check output
    assert "homebrew.mxcl.demo-backup" in output
    # item 1: configured items that match nothing render the dim hint
    assert "ghost-service" in output
    # item 4: the meaning/values/cost block is rendered once per key, not twice
    # (rich wraps long lines, so compare on whitespace-normalized output)
    normalized = " ".join(output.split())
    assert normalized.count(t("power.sleep.desc")) == 1


def test_check_and_tui_share_one_service_renderer(fake_runner, tmp_path):
    from travel_mode.system.launchd import service_information, svc_scan

    _configure(tmp_path)
    fake_runner.responses[("launchctl", "list")] = CmdResult("101\t0\thomebrew.mxcl.demo-backup\n", "", 0)

    svc = svc_scan(load_config())

    # a single renderer feeds mode_check, build_svc and the unmatched dim hints
    assert [row["label"] for row in svc["unloadable"]] == ["homebrew.mxcl.demo-backup"]
    lines = service_information(svc)
    assert any("ghost-service" in line for line in lines)

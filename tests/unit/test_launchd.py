"""LaunchAgent discovery classification tests (isolated fixtures, sanitized labels)."""

from __future__ import annotations

import plistlib

import pytest

from travel_mode.config import Config, ServicesConfig
from travel_mode.i18n import t
from travel_mode.system import launchd


@pytest.fixture(autouse=True)
def english_messages(monkeypatch):
    monkeypatch.setattr("travel_mode.i18n._language", "en")


def _plist(path, label):
    with path.open("wb") as handle:
        plistlib.dump({"Label": label, "ProgramArguments": ["/bin/true"]}, handle)


def _agents(tmp_path, plists):
    home = tmp_path / "home"
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    for name, label in plists.items():
        _plist(agents / name, label)
    return home, agents


def test_svc_scan_classifies_loaded_agents_and_ignores_apple(tmp_path, monkeypatch):
    home, agents = _agents(
        tmp_path,
        {
            "unload.plist": "com.example.unload",
            "keep.plist": "homebrew.mxcl.keep",
            "candidate.plist": "com.example.candidate",
            "apple.plist": "com.apple.example",
        },
    )
    agents.joinpath("broken.plist").write_text("not a plist", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    def fake_run_out(command, timeout=None, sudo=False, interactive=False):
        if command[:2] == ["launchctl", "list"]:
            return (
                "PID\tStatus\tLabel\n"
                "101\t0\tcom.example.unload\n"
                "-\t0\thomebrew.mxcl.keep\n"
                "202\t0\tcom.example.candidate\n"
                "303\t0\tcom.apple.example\n",
                0,
            )
        if command[:2] == ["ps", "-p"]:
            return ("1.25\n", 0)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(launchd, "run_out", fake_run_out)
    monkeypatch.setattr(launchd, "run_rc", fake_run_out)
    config = Config(services=ServicesConfig(unload=("com.example.unload",), keep=("homebrew.mxcl.keep",)))

    result = launchd.svc_scan(config)

    assert [row["label"] for row in result["unloadable"]] == ["com.example.unload"]
    assert result["unloadable"][0]["pid"] == "101"
    assert result["unloadable"][0]["cpu"] == "1.25"
    assert result["candidates"][0]["label"] == "com.example.candidate"
    assert result["kept"] == ["homebrew.mxcl.keep"]
    assert all(row["label"] != "com.apple.example" for row in result["candidates"])
    assert result["invalid"][0]["plist_path"].endswith("broken.plist")
    assert "error" in result["invalid"][0]


def test_match_services_priority_filename_glob_substring_and_tokens():
    rows = [
        {"label": "com.example.backup", "plist_path": "/tmp/backup.plist"},
        {"label": "com.example.backup-helper", "plist_path": "/tmp/helper.plist"},
        {"label": "homebrew.mxcl.demo-sync", "plist_path": "/tmp/sync.plist"},
    ]
    # 1. exact label wins even though a glob would match more rows
    assert [row["label"] for row in launchd.match_services("com.example.backup", rows)] == [
        "com.example.backup"
    ]
    # 2. exact plist file name, with or without suffix
    assert [row["label"] for row in launchd.match_services("helper.plist", rows)] == [
        "com.example.backup-helper"
    ]
    assert [row["label"] for row in launchd.match_services("sync", rows)] == ["homebrew.mxcl.demo-sync"]
    # 3. glob, every match included
    assert {row["label"] for row in launchd.match_services("*backup*", rows)} == {
        "com.example.backup",
        "com.example.backup-helper",
    }
    # 4. case-insensitive substring
    assert launchd.match_services("DEMO-SYNC", rows)[0]["label"] == "homebrew.mxcl.demo-sync"
    # 5. '-' tokens: every token must appear, order independent
    assert launchd.match_services("sync-demo", rows)[0]["label"] == "homebrew.mxcl.demo-sync"
    assert launchd.match_services("sync demo", rows) == []


def test_svc_scan_short_config_name_matches_and_unmatched_is_reported(tmp_path, monkeypatch):
    _ = _agents(
        tmp_path,
        {
            "backup-one.plist": "com.example.backup-one",
            "backup-two.plist": "com.example.backup-two",
            "sync.plist": "homebrew.mxcl.demo-sync",
        },
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def fake_run_out(command, timeout=None, sudo=False, interactive=False):
        if command[:2] == ["launchctl", "list"]:
            return (
                "PID\tStatus\tLabel\n"
                "101\t0\tcom.example.backup-one\n"
                "102\t0\tcom.example.backup-two\n"
                "103\t0\thomebrew.mxcl.demo-sync\n",
                0,
            )
        if command[:2] == ["ps", "-p"]:
            return ("", 0)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(launchd, "run_out", fake_run_out)
    config = Config(services=ServicesConfig(unload=("backup", "no-such-service")))

    result = launchd.svc_scan(config)

    # one config item may match several loaded services; all are included
    assert {row["label"] for row in result["unloadable"]} == {
        "com.example.backup-one",
        "com.example.backup-two",
    }
    assert result["unmatched"] == [{"group": "unload", "item": "no-such-service"}]
    # service_information is the single renderer shared by check and the TUI page
    lines = launchd.service_information(result)
    assert any("no-such-service" in line for line in lines)


def test_invalid_plists_collapse_to_one_summary_with_deduped_details(tmp_path, monkeypatch):
    home, agents = _agents(tmp_path, {})
    (agents / "broken.plist").write_text("not a plist", encoding="utf-8")
    _plist(agents / "missing-label.plist", "")
    monkeypatch.setenv("HOME", str(home))

    def fake_run_out(command, timeout=None, sudo=False, interactive=False):
        if command[:2] == ["launchctl", "list"]:
            return ("PID\tStatus\tLabel\n", 0)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(launchd, "run_out", fake_run_out)
    config = Config()

    # the same directory twice must not duplicate invalid entries
    result = launchd.svc_scan(config, directories=(agents, agents))

    assert len(result["invalid"]) == 2
    info = launchd.service_information(result)
    summary = t("services.invalid_summary", count=len(result["invalid"]))
    # exactly one collapsed summary line, no per-plist detail lines
    assert sum(1 for line in info if line == summary) == 1
    details = launchd.invalid_plist_details(result)
    assert len(details) == 2
    assert len(set(details)) == 2
    # details never leak into the shared page/check rendering
    assert not any(detail in info for detail in details)

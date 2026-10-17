"""Public rules, identity precedence and category batches use only local fixtures."""

import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from travel_mode.categories import app_action, identify, pause_categories, rules
from travel_mode.config import AppsConfig, CategoriesConfig, Config, OptionsConfig, ServicesConfig
from travel_mode.profiles import profile_scope
from travel_mode.system import apps, launchd
from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize("rule", rules(), ids=lambda rule: rule["id"])
def test_each_public_rule_matches_and_documents_source(rule):
    fields = {"apps": "name", "labels": "label", "bundle_ids": "bundle_id", "executables": "executable"}
    for key, argument in fields.items():
        for pattern in rule[key]:
            sample = pattern.replace("*", "fixture")
            assert identify(**{argument: sample})["id"] == rule["id"]
    source = Path(__file__).parents[2] / "src/travel_mode/categories.toml"
    section = source.read_text().split(f'id = "{rule["id"]}"')[0].rsplit("# Source:", 1)[1]
    assert "https://" in section and "# Public identity:" in section


def test_category_service_selection_keep_ignore_dedup_and_apple(fake_runner, tmp_path):
    directory = tmp_path / "agents"
    directory.mkdir()
    labels = ["com.google.keystone.agent", "org.fixture.restic.backup", "com.apple.bird"]
    for index, label in enumerate([*labels, labels[0]]):
        (directory / f"{index}.plist").write_bytes(plistlib.dumps({"Label": label}))
    fake_runner.responses[("launchctl", "list")] = CmdResult(
        "\n".join(f"- 0 {label}" for label in labels), "", 0
    )
    config = replace(
        Config.defaults(),
        options=OptionsConfig(scenario="work"),
        services=ServicesConfig(unload=("*keystone*", "*restic*"), keep=("*keystone*",)),
    )
    scan = launchd.svc_scan(config, directories=(directory,))
    assert [row["label"] for row in scan["unloadable"]] == [labels[1]]
    assert scan["kept"] == [labels[0]]
    assert scan["notices"][0]["rule_id"] == "icloud"
    assert len(scan["recognized"]) == 3
    ignored = replace(config, services=ServicesConfig(), categories=CategoriesConfig(ignore=("restic",)))
    scan = launchd.svc_scan(ignored, directories=(directory,))
    assert [row["label"] for row in scan["unloadable"]] == [labels[0]]


def test_service_executable_rule_and_app_skip_priority(fake_runner, tmp_path):
    path = tmp_path / "fixture.plist"
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": "org.fixture.worker",
                "ProgramArguments": [
                    "/Applications/Fixture.app/Contents/Frameworks/Sparkle.framework/Versions/B/Updater.app/Contents/MacOS/Updater"
                ],
            }
        )
    )
    fake_runner.responses[("launchctl", "list")] = CmdResult("- 0 org.fixture.worker", "", 0)
    config = replace(Config.defaults(), options=OptionsConfig(scenario="work"))
    scan = launchd.svc_scan(config, directories=(tmp_path,))
    assert scan["candidates"][0]["category_action"] == "notice"
    assert not scan["unloadable"]


def test_work_does_not_apply_away_builtin_app_defaults():
    config = Config.defaults()
    with profile_scope(config, "work") as effective:
        assert app_action("Slack", None, effective) == "ask"
        assert app_action("Docker", identify(name="Docker"), effective) == "ask"
        assert app_action("Google Drive", identify(name="Google Drive"), effective) == "quit"


def test_explicit_app_actions_override_categories_and_ignore():
    config = replace(
        Config.defaults(),
        options=OptionsConfig(scenario="work"),
        apps=AppsConfig(configured_actions=("Google Drive",), freeze=("Google Drive",)),
    )
    rule = identify(name="Google Drive")
    assert app_action("Google Drive", rule, config) == "freeze"
    assert (
        app_action("Google Drive", rule, replace(config, apps=AppsConfig(skip=("Google Drive",)))) == "skip"
    )
    assert app_action("Arq", identify(name="Arq"), config) == "skip"


def test_app_scanning_discovers_bundle_id_once_and_respects_ignore(fake_runner, monkeypatch, tmp_path):
    root = tmp_path / "Applications"
    path = root / "Renamed Product.app"
    (path / "Contents").mkdir(parents=True)
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.google.drivefs"}))
    monkeypatch.setattr(apps, "APP_DIRS", [str(root)])
    fake_runner.responses[("ps", "-axo", "pid=,%cpu=,command=")] = CmdResult(
        f"900001 0.5 {path}/Contents/MacOS/Google Drive", "", 0
    )
    with profile_scope(Config.defaults(), "work"):
        rows = apps.scan_apps()["running"]
        assert len(rows) == 1 and rows[0]["rule_id"] == "google-drive" and rows[0]["defact"] == "quit"
    config = replace(Config.defaults(), apps=AppsConfig(ignore=("Renamed Product",)))
    with profile_scope(config, "work"):
        assert not apps.scan_apps()["running"]


def test_one_click_batches_deduplicate_and_keep_explicit_exclusions():
    service = {"label": "org.fixture.restic", "plist_path": "/tmp/fixture.plist"}
    kept = {"label": "com.google.keystone.agent", "plist_path": "/tmp/keystone.plist"}
    app = {"name": "Google Drive", "path": "/Applications/Google Drive.app"}
    skipped = {"name": "Dropbox", "path": "/Applications/Dropbox.app"}
    calls, saves = [], []

    def execute(action):
        calls.append(action)
        return ("", 0) if hasattr(action, "command") else True

    config = replace(
        Config.defaults(), services=ServicesConfig(keep=("*keystone*",)), apps=AppsConfig(skip=("Dropbox",))
    )
    with profile_scope(config, "work"):
        pause_categories(
            {"sync", "backup", "updater"},
            [app, app, skipped],
            [service, service, kept],
            execute,
            lambda: saves.append(True),
            {},
        )
    assert [type(action).__name__ for action in calls] == ["UnloadAgent", "QuitApp"]
    assert len(saves) == 2


def test_one_click_failure_does_not_abort_other_services():
    service = {"label": "org.fixture.restic", "plist_path": "/tmp/fixture.plist"}
    attempted, saved = [], []
    other = {"label": "org.fixture.restic.second", "plist_path": "/tmp/second.plist"}

    def execute(action):
        attempted.append(action.path)
        return ("failure", 5) if action.path == service["plist_path"] else ("", 0)

    pause_categories({"backup"}, [], [service, other], execute, lambda: saved.append(True), {})
    assert attempted == [service["plist_path"], other["plist_path"]]
    assert saved == [True]

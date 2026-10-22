"""Personal defaults are explicit, scenario-specific and atomically persisted."""

import os
import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from travel_mode import app_policy
from travel_mode.categories import app_action, identify
from travel_mode.config import Config, ConfigError, load_config, preview_app_choices, write_app_choices
from travel_mode.flows import setup
from travel_mode.i18n import set_language
from travel_mode.profiles import profile_scope
from travel_mode.system import apps
from travel_mode.system.shell import CmdResult


@pytest.fixture(autouse=True)
def english():
    set_language("en")


def installed(tmp_path, fake_runner):
    rows = []
    for pid, name in enumerate(("WeChat", "Google Drive", "UURemote", "Google Chrome"), 910001):
        path = tmp_path / "Applications" / f"{name}.app"
        (path / "Contents").mkdir(parents=True)
        (path / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundlePackageType": "APPL", "CFBundleIdentifier": "org.fixture." + str(pid)})
        )
        rows.append(f"{pid} 0.0 {path}/Contents/MacOS/{name}")
    fake_runner.responses[("ps", "-axo", "pid=,%cpu=,command=")] = CmdResult("\n".join(rows), "", 0)
    fake_runner.responses[("pmset", "-g", "assertions")] = CmdResult(
        'Assertion status system-wide:\npid 910003(UURemote): [0x1] PreventSystemSleep named: "remote"', "", 0
    )


@pytest.mark.parametrize(
    "name,assertion,expected",
    [
        ("Ordinary App", False, ("freeze", "quit", "freeze")),
        ("WeChat", False, ("freeze", "quit", "freeze")),
        ("UURemote", True, ("freeze", "quit", "assertion")),
        ("Google Drive", False, ("quit", "quit", "provider")),
        ("OneDrive", False, ("quit", "quit", "provider")),
        ("Dropbox", False, ("quit", "quit", "provider")),
        ("Google Chrome", False, ("ask", "ask", "interactive")),
        ("Visual Studio Code", False, ("ask", "ask", "interactive")),
        ("Terminal", False, ("ask", "ask", "interactive")),
    ],
)
def test_recommendation_rules(name, assertion, expected):
    row = app_policy.recommend(name, rule=identify(name=name), assertion=assertion)
    assert tuple(row[k] for k in ("work", "away", "reason")) == expected


def test_recommendation_uses_bundle_identity():
    assert app_policy.recommend("Renamed", bundle_id="com.google.Chrome")["work"] == "ask"
    assert (
        app_policy.recommend("Renamed", rule=identify(bundle_id="com.google.drivefs"))["reason"] == "provider"
    )


def test_single_snapshot_maps_assertion_pid_and_adaptive_scan(tmp_path, fake_runner):
    installed(tmp_path, fake_runner)
    rows = app_policy.discover()
    assert {r["name"]: (r["work"], r["away"]) for r in rows} == {
        "WeChat": ("freeze", "quit"),
        "Google Drive": ("quit", "quit"),
        "UURemote": ("freeze", "quit"),
        "Google Chrome": ("ask", "ask"),
    }
    assert sum(c[0] == "mdfind" for c in fake_runner.calls) == 1
    assert fake_runner.calls.count(("ps", "-axo", "pid=,%cpu=,command=")) == 1
    assert fake_runner.calls.count(("pmset", "-g", "assertions")) == 1
    for scenario in ("work", "away"):
        with profile_scope(Config.defaults(), None, scenario=scenario):
            scan = apps.scan_apps()["running"]
        remote = next(r for r in scan if r["name"] == "UURemote")
        assert remote["defact"] == ("freeze" if scenario == "work" else "quit")
        assert remote["reason"] == "assertion"


def test_scenario_override_beats_global_and_legacy_file_is_unchanged(tmp_path):
    path = tmp_path / "config.toml"
    original = '# untouched\n[apps]\nfreeze=["OneDrive"]\nskip=["Editor"]\n[apps.away]\nquit=["Editor"]\n'
    path.write_text(original)
    config = load_config(path)
    assert config.apps.policy == "legacy"
    for scenario in ("work", "away"):
        with profile_scope(config, None, scenario=scenario) as effective:
            assert app_action("OneDrive", identify(name="OneDrive"), effective) == "freeze"
            assert app_action("Editor", None, effective) == ("quit" if scenario == "away" else "skip")
            assert app_action("New App", None, effective) == "ask"
    assert path.read_text() == original
    with profile_scope(
        replace(config, apps=replace(config.apps, policy="adaptive")), None, scenario="away"
    ) as effective:
        assert app_action("OneDrive", identify(name="OneDrive"), effective) == "freeze"


@pytest.mark.parametrize(
    "content,key",
    [
        ("[apps.work]\nbogus=[]", "apps.work.bogus"),
        ("[apps.away]\nquit=3", "apps.away.quit"),
        ('[apps.work]\nquit=["A"]\nfreeze=["A"]', "apps.work.freeze[0]"),
        ("[apps]\nwork=3", "work"),
        ('[apps]\npolicy="bad"', "apps.policy"),
        ("[apps]\npolicy=[]", "apps.policy"),
        ("[apps]\nsetup_completed=3", "apps.setup_completed"),
    ],
)
def test_scenario_config_error_context(tmp_path, content, key):
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert str(path) in str(error.value) and key in str(error.value)


def test_atomic_choices_preserve_comments_and_other_tables(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '# heading\n[options]\nlanguage = "zh" # keep this\n'
        '[apps]\nquit=["Music"] # old\n[apps.work]\nfreeze=["Other", "Chrome"]\n'
    )
    write_app_choices(
        {"work": {"Chrome": "quit"}, "away": {"Chrome": "freeze"}}, completed=True, adaptive=True
    )
    text = path.read_text()
    assert "# heading" in text and "# keep this" in text and "# old" in text
    config = load_config(path)
    assert config.apps.work == {"Other": "freeze", "Chrome": "quit"}
    assert config.apps.away == {"Chrome": "freeze"}
    assert config.apps.setup_completed and config.apps.policy == "adaptive"
    assert config.apps.quit == ("Music",)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "stage",
    [
        "scan_index",
        "scan_processes",
        "scan_identities",
        "scan_assertions",
        "scan_foreground",
        "scan_foreground_pid",
        "mkdir",
        "temporary",
        "write",
        "flush",
        "fsync",
        "validate",
        "replace",
    ],
)
def test_setup_fault_injection_preserves_original_and_logs(tmp_path, fake_runner, monkeypatch, capsys, stage):
    import tempfile

    import travel_mode.config as config_api

    path = tmp_path / "config.toml"
    original = '# original\n[options]\nlanguage="en"\n'
    path.write_text(original)
    installed(tmp_path, fake_runner)
    if stage.startswith("scan_"):
        command = {
            "scan_index": ("mdfind",),
            "scan_processes": ("ps", "-axo", "pid=,%cpu=,command="),
            "scan_identities": ("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm="),
            "scan_assertions": ("pmset", "-g", "assertions"),
            "scan_foreground": ("lsappinfo", "front"),
            "scan_foreground_pid": ("lsappinfo", "info"),
        }[stage]
        fake_runner.responses[command] = CmdResult("", "injected read failure", 1)
    else:

        def broken(*args, **kwargs):
            raise OSError("injected write failure")

        if stage == "validate":
            original_load = config_api.load_config

            def validate(path=None):
                if path and Path(path).name.startswith(".apps-"):
                    raise ConfigError(path, "apps", "injected invalid candidate")
                return original_load(path)

            monkeypatch.setattr(config_api, "load_config", validate)
        elif stage in {"mkdir", "write", "flush", "replace"}:
            method = {"mkdir": "mkdir", "write": "write_bytes", "flush": "write_bytes", "replace": "replace"}[
                stage
            ]
            # The actual temporary write is the binary file handle below, not Path.write_bytes.
            if stage in {"write", "flush"}:
                original_fdopen = tempfile.NamedTemporaryFile

                class BadWrite:
                    def __init__(self, *a, **kw):
                        self.handle = original_fdopen(*a, **kw)
                        self.name = self.handle.name

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        self.handle.close()

                    def write(self, data):
                        if stage == "write":
                            raise OSError("injected write failure")
                        return self.handle.write(data)

                    def flush(self):
                        raise OSError("injected flush failure")

                monkeypatch.setattr(tempfile, "NamedTemporaryFile", BadWrite)
            else:
                old = getattr(Path, method)

                def selective(self, *a, **kw):
                    if (stage == "mkdir" and self == path.parent) or (
                        stage == "replace" and self.name.startswith(".apps-")
                    ):
                        # One-shot failure permits the failure logger to write its receipt.
                        monkeypatch.setattr(Path, method, old)
                        return broken()
                    return old(self, *a, **kw)

                monkeypatch.setattr(Path, method, selective)
        elif stage == "temporary":
            monkeypatch.setattr(tempfile, "NamedTemporaryFile", broken)
        else:
            old = os.fsync

            def once(fd):
                monkeypatch.setattr(os, "fsync", old)
                return broken()

            monkeypatch.setattr(os, "fsync", once)
    assert setup.run(yes=True) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err and "travel-mode.log" in captured.err
    assert path.read_text() == original
    assert not list(tmp_path.glob(".apps-*"))
    assert (tmp_path / "travel-mode.log").exists()


def test_cli_setup_accepts_recommendations_and_dry_run(tmp_path, fake_runner, capsys):
    installed(tmp_path, fake_runner)
    assert setup.run(yes=True, dry_run=True) == 0
    assert not (tmp_path / "config.toml").exists()
    output = capsys.readouterr().out
    assert 'policy = "adaptive"' in output and "On the go freeze 2, quit 1" in output
    answers = iter(["", "", "", "", "", "", "", "", "", "y"])
    assert setup.run(input_fn=lambda prompt: next(answers)) == 0
    config = load_config()
    assert config.apps.work["Google Drive"] == "quit"
    assert config.apps.away["UURemote"] == "quit"
    assert capsys.readouterr().out.count("Saved.") == 1


def test_remember_accept_reject_and_no_repeat(tmp_path):
    memory = setup.Remember()
    assert memory.offer("Chrome", "work", "freeze", "ask")
    assert memory.answer("Chrome", "work", "freeze", False) is None
    assert not memory.offer("Chrome", "work", "freeze", "ask")
    assert not (tmp_path / "config.toml").exists()
    assert memory.offer("Chrome", "away", "quit", "ask")
    assert memory.answer("Chrome", "away", "quit", True) == "Saved."
    assert load_config().apps.away == {"Chrome": "quit"}
    assert not memory.offer("Chrome", "away", "quit", "ask")
    assert not memory.offer("Other", "away", "freeze", "freeze")


def test_preview_does_not_write(tmp_path):
    text = preview_app_choices({}, completed=True, adaptive=True)
    assert "setup_completed = true" in text
    assert not (tmp_path / "config.toml").exists()


def test_foreground_app_is_not_an_automatic_target(tmp_path, fake_runner):
    installed(tmp_path, fake_runner)
    fake_runner.responses[("lsappinfo", "info")] = CmdResult("pid = 910001", "", 0)
    rows = app_policy.discover()
    assert "WeChat" not in {row["name"] for row in rows}
    with profile_scope(Config.defaults(), None, scenario="away"):
        assert next(r for r in apps.scan_apps()["running"] if r["name"] == "WeChat")["defact"] == "skip"


def test_setup_command_routes_cli_and_dry_run(tmp_path, fake_runner, darwin_platform, capsys, monkeypatch):
    from travel_mode.cli import main

    installed(tmp_path, fake_runner)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("dry-run must not prompt"))
    assert main(["setup", "--no-tui", "--dry-run", "--lang", "en"]) == 0
    assert not (tmp_path / "config.toml").exists()
    assert 'policy = "adaptive"' in capsys.readouterr().out
    assert main(["setup", "--no-tui", "--yes", "--lang", "en"]) == 0
    assert load_config().apps.setup_completed


def test_remember_cli_writes_once_and_decline_is_session_local(tmp_path):
    seen = []
    memory = setup.Remember()

    def no(prompt):
        seen.append(prompt)
        return "n"

    memory.cli("Google Chrome", "work", "freeze", "ask", input_fn=no)
    memory.cli("Google Chrome", "work", "freeze", "ask", input_fn=no)
    assert len(seen) == 1 and not (tmp_path / "config.toml").exists()
    memory = setup.Remember()
    memory.cli("Google Chrome", "work", "freeze", "ask", input_fn=lambda _: "y")
    assert load_config().apps.work["Google Chrome"] == "freeze"


def test_foreground_guard_prevents_mutation(fake_runner):
    from travel_mode.system.process_control import Process, _protected

    rows = [Process(910001, 1, "S", "fixture-start", "/Applications/Fixture.app/Contents/MacOS/Fixture")]
    fake_runner.responses[("lsappinfo", "info")] = CmdResult("pid = 910001", "", 0)
    with pytest.raises(PermissionError, match="foreground"):
        _protected("/Applications/Fixture.app", rows)
    assert not any(c[0] in {"kill", "osascript"} for c in fake_runner.calls)


def test_cli_skip_sets_only_marker_and_uses_builtin_defaults(tmp_path, fake_runner):
    assert setup.run(input_fn=lambda _: "s") == 0
    config = load_config()
    assert config.apps.setup_completed and config.apps.policy == "adaptive"
    assert config.apps.work == config.apps.away == {}
    assert not fake_runner.calls


@pytest.mark.parametrize("policy", ["legacy", "adaptive"])
def test_scenario_choice_for_installed_filename_beats_bundle_display_alias(tmp_path, fake_runner, policy):
    path = tmp_path / "Applications/Renamed Browser.app"
    (path / "Contents").mkdir(parents=True)
    (path / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundlePackageType": "APPL",
                "CFBundleIdentifier": "com.google.Chrome",
                "CFBundleDisplayName": "Google Chrome",
            }
        )
    )
    (tmp_path / "config.toml").write_text(
        f'[apps]\npolicy="{policy}"\n[apps.away]\nquit=["Renamed Browser"]\n'
    )
    fake_runner.responses[("ps", "-axo", "pid=,%cpu=,command=")] = CmdResult(
        f"910001 0.0 {path}/Contents/MacOS/Browser", "", 0
    )
    with profile_scope(load_config(), None, scenario="away"):
        rows = apps.scan_apps()["running"]
    assert len(rows) == 1 and rows[0]["defact"] == "quit"


def test_setup_log_write_failure_is_explicit_and_does_not_escape(tmp_path, monkeypatch):
    from travel_mode import failures
    from travel_mode.state import StateError

    def denied(*args, **kwargs):
        raise StateError("injected log permission error")

    monkeypatch.setattr(failures, "append_log", denied)
    text = setup.failure(OSError("original setup error"))
    assert "original setup error" in text and "injected log permission error" in text
    assert "travel-mode.log" in text and "Traceback" not in text


def test_setup_rejects_unrecognizable_assertion_read(tmp_path, fake_runner):
    from travel_mode.system.shell import ReadError

    installed(tmp_path, fake_runner)
    fake_runner.responses[("pmset", "-g", "assertions")] = CmdResult("unexpected output", "", 0)
    with pytest.raises(ReadError):
        app_policy.discover()


def test_setup_tui_command_prints_one_saved_result(tmp_path, darwin_platform, monkeypatch, capsys):
    from travel_mode.cli import main
    from travel_mode.tui.setup import SetupApp

    monkeypatch.setattr(SetupApp, "run", lambda self: "Saved.")
    assert main(["setup", "--tui", "--lang", "en"]) == 0
    assert capsys.readouterr().out == "Saved.\n"


def test_scenario_edit_retains_array_item_and_standalone_comments(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[apps.work]\nfreeze = [\n # section note\n "Chrome", # chosen note\n'
        ' "Other", # retained note\n] # array note\n'
    )
    write_app_choices({"work": {"Chrome": "quit"}})
    text = path.read_text()
    for note in ("# section note", "# chosen note", "# retained note", "# array note"):
        assert text.count(note) == 1
    config = load_config()
    assert config.apps.work == {"Chrome": "quit", "Other": "freeze"}


@pytest.mark.parametrize("language", ["en", "zh"])
def test_cli_setup_uses_localized_scenarios(tmp_path, monkeypatch, capsys, language):
    from travel_mode.i18n import set_language, t

    set_language(language)
    monkeypatch.setattr(
        app_policy,
        "discover",
        lambda: [
            dict(name="Fixture", work="freeze", away="freeze", reason="freeze"),
        ],
    )
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return ""

    assert setup.run(input_fn=answer) == 0
    output = capsys.readouterr().out
    assert t("scenario.work") in output and t("scenario.away") in output
    assert "work:" not in output and "away:" not in output
    assert t("scenario.work") in prompts[1] and t("scenario.away") in prompts[2]
    assert not (tmp_path / "config.toml").exists()


@pytest.mark.parametrize("language", ["en", "zh"])
def test_freeze_reason_preserves_state_without_message_guarantees(language):
    from travel_mode.i18n import t

    set_language(language)
    expected = (
        "On the go: keep the running state; overnight: quit and reopen on restore."
        if language == "en"
        else "外出时保留运行状态；过夜时退出，恢复时重新打开。"
    )
    for name in ("Calculator", "Books", "Photos", "Preview", "WeChat"):
        row = dict(name=name, **app_policy.recommend(name))
        selected = app_policy.choices([row], Config())
        assert expected in app_policy.row_text(row, selected)
        assert t("setup.short_freeze") == (
            "Keep state; resume" if language == "en" else "保留状态，恢复后继续"
        )


def test_box_is_file_provider_and_nextcloud_mode_is_not_assumed():
    assert identify(name="Box")["file_provider"] is True
    assert app_policy.recommend("Box", rule=identify(name="Box")) == dict(
        work="quit", away="quit", reason="provider"
    )
    assert not identify(name="Nextcloud").get("file_provider")
    assert app_policy.recommend("Nextcloud", rule=identify(name="Nextcloud")) == dict(
        work="freeze", away="quit", reason="freeze"
    )


@pytest.mark.parametrize("kind", ["terminal", "terminal_alias", "apple", "ancestor", "foreground"])
def test_setup_discovery_excludes_protected_apps(tmp_path, fake_runner, monkeypatch, capsys, kind):
    from types import SimpleNamespace

    from travel_mode.system import process_control

    installed(tmp_path, fake_runner)
    name = "Terminal" if kind == "terminal" else "Protected Fixture"
    bid = {"terminal_alias": "com.googlecode.iterm2", "apple": "com.apple.Preview"}.get(
        kind, "org.fixture.protected"
    )
    path = tmp_path / "Applications" / f"{name}.app"
    (path / "Contents").mkdir(parents=True)
    (path / "Contents/Info.plist").write_bytes(
        plistlib.dumps({"CFBundlePackageType": "APPL", "CFBundleIdentifier": bid})
    )
    snapshot_cmd = ("ps", "-axo", "pid=,%cpu=,command=")
    fake_runner.responses[snapshot_cmd] = CmdResult(
        fake_runner.responses[snapshot_cmd].stdout + f"\n910009 0.0 {path}/Contents/MacOS/Fixture", "", 0
    )
    monkeypatch.setattr(process_control, "os", SimpleNamespace(getpid=lambda: 910011, getppid=lambda: 910010))
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        f"910010 910009 S Thu Oct 15 12:00:00 2026 /fixture/parent\n"
        f"910009 1 S Thu Oct 15 12:00:00 2026 {path}/Contents/Resources/Fixture"
        if kind == "ancestor"
        else "",
        "",
        0,
    )
    if kind == "foreground":
        fake_runner.responses[("lsappinfo", "info")] = CmdResult("pid = 910009", "", 0)
    rows = app_policy.discover()
    assert name not in {row["name"] for row in rows}
    assert "WeChat" in {row["name"] for row in rows}
    identity_cmd = ("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")
    assert fake_runner.calls.count(identity_cmd) == 1
    assert setup.run(yes=True, dry_run=True) == 0
    assert name not in capsys.readouterr().out

"""Fresh machines share safe setup recommendations, previews and atomic writes."""

import plistlib
from pathlib import Path

import pytest

from travel_mode import onboarding
from travel_mode.config import load_config, write_app_choices
from travel_mode.flows import setup
from travel_mode.i18n import set_language
from travel_mode.system.shell import CmdResult


def international(tmp_path, fake_runner):
    products = {
        "Drive Client": "com.google.drivefs",
        "Cisco Secure Client": "com.cisco.secureclient.gui",
        "Sketchbook": "org.example.sketchbook",
        "Terminal": "com.apple.Terminal",
    }
    for name, bid in products.items():
        path = tmp_path / "Applications" / (name + ".app")
        (path / "Contents").mkdir(parents=True)
        (path / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundlePackageType": "APPL", "CFBundleIdentifier": bid})
        )
    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    for label in ("homebrew.mxcl.syncthing", "com.apple.protected", "org.example.idle", "org.example.busy"):
        (agents / (label + ".plist")).write_bytes(plistlib.dumps({"Label": label}))
    fake_runner.responses[("launchctl", "list")] = CmdResult(
        "- 0 homebrew.mxcl.syncthing\n- 0 com.apple.protected\n"
        "- 0 org.example.idle\n910021 0 org.example.busy",
        "",
        0,
    )
    fake_runner.responses[("ps", "-p", "910021", "-o", "%cpu=")] = CmdResult("45", "", 0)


@pytest.mark.parametrize("populated", [False, True])
def test_blank_and_international_mac_preview_and_write(tmp_path, fake_runner, populated, capsys):
    if populated:
        international(tmp_path, fake_runner)
    data = onboarding.scan()
    selected = onboarding.choices(data)
    assert "Terminal" not in selected["work"]
    if populated:
        assert selected["work"] == {
            "Drive Client": "quit",
            "Cisco Secure Client": "skip",
            "Sketchbook": "freeze",
        }
        assert selected["away"]["Sketchbook"] == "quit"
        assert selected["services"] == {"homebrew.mxcl.syncthing": "unload", "org.example.busy": "unload"}
        assert selected["network"] == ["Cisco Secure Client"]
    else:
        assert selected == {"work": {}, "away": {}, "services": {}, "network": []}
    assert setup.run(yes=True, dry_run=True) == 0
    assert not (tmp_path / "config.toml").exists()
    assert setup.run(yes=True) == 0
    result = load_config()
    assert result.apps.work == selected["work"] and result.apps.away == selected["away"]
    assert set(result.services.unload) == set(selected["services"])
    assert list(result.network.apps) == selected["network"]
    assert not any(call[0] in {"sudo", "kill", "osascript", "open"} for call in fake_runner.calls)


def test_recommendations_keep_existing_choices_and_comments(tmp_path, fake_runner):
    international(tmp_path, fake_runner)
    path = tmp_path / "config.toml"
    path.write_text(
        '# header\n[apps]\npolicy="adaptive"\n[apps.work]\nquit=["Sketchbook"] # mine\n'
        '[services]\nkeep=["*syncthing*"] # service note\n'
        '[network]\napps=["My VPN"] # network note\n'
    )
    data = onboarding.scan()
    selected = onboarding.choices(data)
    assert selected["work"]["Sketchbook"] == "quit"
    assert selected["services"]["homebrew.mxcl.syncthing"] == "keep"
    assert next(row for row in data["apps"] if row["name"] == "Sketchbook")["configured"]
    write_app_choices(selected, completed=True, adaptive=True)
    text = path.read_text()
    assert all(note in text for note in ("# header", "# mine", "# service note", "# network note"))
    assert load_config().network.apps == ("My VPN", "Cisco Secure Client")


def test_international_network_registry_discovers_installed_identities_without_default_names(tmp_path):
    from travel_mode.categories import rules
    from travel_mode.network_apps import network_scan

    names = set()
    for rule in rules():
        if rule["category"] != "network":
            continue
        name = "Renamed " + rule["id"] if rule["bundle_ids"] else rule["apps"][0]
        names.add(name)
        info = tmp_path / "Applications" / (name + ".app") / "Contents/Info.plist"
        info.parent.mkdir(parents=True)
        info.write_bytes(
            plistlib.dumps(
                {"CFBundlePackageType": "APPL", "CFBundleIdentifier": next(iter(rule["bundle_ids"]), "")}
            )
        )
    assert {row["name"] for row in network_scan()} == names
    selected = onboarding.choices(onboarding.scan())
    assert set(selected["network"]) == names
    assert all(selected[scenario] == dict.fromkeys(names, "skip") for scenario in ("work", "away"))


def test_setup_scan_deadline_is_explicit_and_restores_runner(fake_runner):
    from travel_mode.system import shell

    ticks = iter([0, 20])
    with pytest.raises(shell.ReadError, match="15"), onboarding.read_budget(clock=lambda: next(ticks)):
        shell._read(["pmset", "-g"])
    assert shell.get_runner() is fake_runner


@pytest.mark.parametrize("stage", ["agent_scan", "write"])
def test_combined_setup_failure_preserves_all_original_tables(tmp_path, fake_runner, monkeypatch, stage):
    international(tmp_path, fake_runner)
    path = tmp_path / "config.toml"
    original = '# original\n[apps]\npolicy="adaptive"\n[services]\nkeep=[]\n[network]\napps=[]\n'
    path.write_text(original)
    if stage == "agent_scan":
        fake_runner.responses[("launchctl", "list")] = CmdResult("", "fixture denied", 1)
    else:
        replace = Path.replace

        def denied(source, destination):
            if source.name.startswith(".apps-"):
                raise OSError("fixture write denied")
            return replace(source, destination)

        monkeypatch.setattr(Path, "replace", denied)
    assert setup.run(yes=True) == 1
    assert path.read_text() == original
    assert not list(tmp_path.glob(".apps-*"))


def test_expected_detection_and_write_wording():
    set_language("zh")
    data = dict(
        apps=[
            dict(group=group)
            for group, count in (("sync-backup", 3), ("updater", 2), ("network", 1), ("other", 18))
            for _ in range(count)
        ],
        services=[],
    )
    assert onboarding.detected(data).startswith(
        "检测到：同步与备份 3 个 · 更新程序 2 个 · 网络与远程 1 个 · 其它 app 18 个"
    )
    selected = {
        "work": {str(i): "freeze" if i < 15 else "quit" for i in range(18)},
        "away": {str(i): "freeze" if i < 12 else "quit" for i in range(18)},
        "services": {str(i): "unload" for i in range(4)},
        "network": ["VPN"],
    }
    selected["work"]["VPN"] = selected["away"]["VPN"] = "skip"
    summary = onboarding.summary(selected, "~/.config/travel-mode/config.toml")
    assert "冻结 15 个、退出 3 个" in summary and "冻结 12 个、退出 6 个" in summary
    assert "暂停后台服务 4 个；网络保持 1 个" in summary


@pytest.mark.parametrize("policy", ["adaptive", "legacy"])
def test_duplicate_names_configure_once_and_target_every_running_copy(tmp_path, fake_runner, policy):
    from travel_mode import app_policy
    from travel_mode.profiles import profile_scope
    from travel_mode.system import apps

    paths = []
    for number in range(4):
        path = tmp_path / f"SDK-{number}" / "Python Launcher.app"
        (path / "Contents").mkdir(parents=True)
        (path / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundlePackageType": "APPL", "CFBundleIdentifier": "org.python.launcher"})
        )
        paths.append(str(path))
    alias = tmp_path / "Python Launcher.app"
    alias.symlink_to(paths[0])
    fake_runner.responses[("mdfind",)] = CmdResult("\n".join([*paths, str(alias)]), "", 0)
    fake_runner.responses[("ps", "-axo", "pid=,%cpu=,command=")] = CmdResult(
        "\n".join(f"{810 + i} 2 {path}/Contents/MacOS/Launcher" for i, path in enumerate(paths)), "", 0
    )
    data = onboarding.scan()
    assert len(data["apps"]) == 1
    row = data["apps"][0]
    assert row["name"] == "Python Launcher" and row["copies"] == 4
    assert set(row["paths"]) == set(paths)
    set_language("zh")
    assert app_policy.display_name(row) == "Python Launcher（4 个副本）"
    assert "Python Launcher（4 个副本）" in app_policy.row_text(row, onboarding.choices(data))
    selected = onboarding.choices(data)
    selected["work"]["Python Launcher"] = "freeze"
    write_app_choices(selected, completed=True, adaptive=policy == "adaptive")
    config = load_config()
    assert config.apps.work == {"Python Launcher": "freeze"}
    if policy == "legacy":
        from dataclasses import replace

        config = replace(config, apps=replace(config.apps, policy="legacy"))
    with profile_scope(config, None, scenario="work"):
        plan = apps.scan_apps()["running"]
    # Aliases may observe the same process but may not schedule a second mutation.
    assert len(plan) == 4
    assert {str(Path(item["path"]).resolve()) for item in plan} == set(paths)
    assert all(item["defact"] == "freeze" for item in plan)


def test_one_foreground_copy_protects_entire_setup_name(tmp_path, fake_runner):
    from travel_mode import app_policy

    rows = [
        dict(
            name="Worker",
            path=str(tmp_path / f"copy-{i}/Worker.app"),
            bundle_id="org.example.worker",
            reason="foreground" if i == 1 else "freeze",
            work="skip" if i == 1 else "freeze",
            away="skip" if i == 1 else "quit",
            running=True,
        )
        for i in range(2)
    ]
    assert app_policy.setup_candidates(rows, []) == []


def test_copies_quit_by_exact_path_and_journal_each_target(tmp_path, fake_runner):
    from test_process_safety import ProcessRunner, _row

    from travel_mode.actions.apps import QuitApp
    from travel_mode.actions.journal import mutation_scope
    from travel_mode.state import new_state, save_state
    from travel_mode.system import shell

    paths = [str(tmp_path / f"sdk-{i}/Python Launcher.app") for i in range(4)]
    rows = {810 + i: _row(path + "/Contents/MacOS/Launcher") for i, path in enumerate(paths)}
    for path in paths:
        info = Path(path) / "Contents/Info.plist"
        info.parent.mkdir(parents=True)
        info.write_bytes(
            plistlib.dumps({"CFBundlePackageType": "APPL", "CFBundleIdentifier": "org.python.launcher"})
        )

    class CopiesRunner(ProcessRunner):
        def __call__(self, cmd, timeout=None, interactive=False):
            if cmd[0] == "osascript":
                self.calls.append(tuple(cmd))
                target = next(path for path in paths if f'tell application "{path}" to quit' in cmd)
                checkpoint = __import__("json").loads((tmp_path / "state.json").read_text())
                assert target in checkpoint["quit_apps"]  # Intent exists before each request.
                rows[810 + paths.index(target)]["stat"] = "gone"
                return CmdResult("", "", 0)
            return super().__call__(cmd, timeout, interactive)

    runner = CopiesRunner(rows)
    shell.set_runner(runner)
    snapshot = new_state()
    with mutation_scope(snapshot, lambda value: save_state(value)):
        for path in paths:
            assert QuitApp(path, "Python Launcher", by_path=True).apply(None)
    assert snapshot["quit_apps"] == paths
    assert all(row["stat"] == "gone" for row in rows.values())
    assert len([call for call in runner.calls if call[0] == "osascript"]) == 4

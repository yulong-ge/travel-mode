"""Phase 6.2 read-only suggestions and tomlkit writes."""

from __future__ import annotations

import plistlib

from travel_mode.config import load_config
from travel_mode.flows import suggest as suggest_module
from travel_mode.flows.suggest import discover_daemon_apps, suggest_data, write_selection


def test_suggest_uses_top_ps_and_assertions(monkeypatch):
    def fake_read(command, timeout=30):
        if command[0] == "top":
            return "PID POWER COMMAND\n123 12.0 Test\n"
        if command[-1] == "assertions":
            return 'pid 123(Test): [0x1] PreventSystemSleep named: "Test"\n'
        raise AssertionError(command)

    monkeypatch.setattr(suggest_module, "read_command", fake_read)
    monkeypatch.setattr(suggest_module, "bundle_for", lambda pid: "/Applications/Test.app")
    monkeypatch.setattr(suggest_module, "svc_scan", lambda config: {"candidates": []})
    data = suggest_data(samples=1)
    assert data["apps"][0]["name"] == "Test"
    assert data["apps"][0]["action"] == "quit"
    assert data["assertions"][0]["pid"] == "123"


def test_tomlkit_suggest_write_preserves_comments(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("# keep this\n[apps]\nwatch = []\nfreeze = []\n", "utf-8")
    write_selection([{"name": "Example", "action": "freeze"}], [], path)
    text = path.read_text("utf-8")
    assert "# keep this" in text and '"Example"' in text
    assert load_config(path).apps.freeze == ("Example",)


def _write_plist(path, value):
    path.write_bytes(plistlib.dumps(value))


def _write_app(root, name, bundle_id):
    info = root / f"{name}.app" / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    _write_plist(info, {"CFBundleIdentifier": bundle_id, "CFBundleName": name})
    return info.parent.parent


def test_daemon_discovery_matches_path_associated_and_bundle_id(tmp_path, monkeypatch):
    system = tmp_path / "Library" / "LaunchDaemons"
    user = tmp_path / "Library" / "LaunchAgents"
    apps = tmp_path / "apps"
    system.mkdir(parents=True)
    user.mkdir(parents=True)
    app_path = _write_app(apps, "Path App", "com.example.path")
    assoc_app = _write_app(apps, "Associated App", "com.example.assoc")
    prefix_app = _write_app(apps, "Prefix App", "com.example.prefix")
    _write_plist(
        system / "com.example.path.daemon.plist",
        {
            "Label": "com.example.path.daemon",
            "KeepAlive": True,
            "Program": str(app_path / "Contents/MacOS/x"),
        },
    )
    _write_plist(
        user / "associated.plist",
        {"Label": "other.assoc", "RunAtLoad": True, "AssociatedBundleIdentifiers": ["com.example.assoc"]},
    )
    _write_plist(
        user / "com.example.prefix.agent.plist", {"Label": "com.example.prefix.agent", "KeepAlive": True}
    )
    _write_plist(user / "com.apple.ignored.plist", {"Label": "com.apple.ignored", "KeepAlive": True})
    (user / "broken.plist").write_text("not plist", encoding="utf-8")
    monkeypatch.setattr(
        "travel_mode.system.daemon_discovery.read_command",
        lambda command, timeout=30: "\n".join(
            str(app / "Contents" / "MacOS" / "daemon") for app in (app_path, assoc_app, prefix_app)
        ),
    )
    rows, warnings = discover_daemon_apps(plist_dirs=(system, user), app_dirs=(apps,), assertion_rows=[])
    assert [row["confidence"] for row in rows] == [
        "path",
        "associated",
        "bundle-id-prefix",
    ]
    assert [row["name"] for row in rows] == ["Path App", "Associated App", "Prefix App"]
    assert len(warnings) == 1 and warnings[0]["path"].endswith("broken.plist")
    assert rows[0]["matches"][0]["kind"] == "system_daemons"
    assert rows[1]["matches"][0]["kind"] == "user_agents"


def test_daemon_write_skips_duplicate_and_low_confidence(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text(
        "[apps]\n[[apps.with_daemon]]\n"
        'name = "Configured"\nsystem_daemons = []\nuser_agents = []\ngroup = "apps"\n',
        encoding="utf-8",
    )
    write_selection(
        [],
        [],
        path,
        daemon_apps=[
            {"name": "Configured", "system_daemons": ["/a"], "user_agents": [], "recommended": True},
            {"name": "Low", "system_daemons": ["/b"], "user_agents": [], "recommended": False},
            {"name": "New", "system_daemons": ["/c"], "user_agents": [], "recommended": True},
        ],
    )
    text = path.read_text(encoding="utf-8")
    assert text.count('name = "Configured"') == 1
    assert text.count('name = "New"') == 1
    assert 'name = "Low"' not in text
    assert "已经配置" in capsys.readouterr().err


def test_remote_daemon_programarguments_recommended_and_configured_is_visible(tmp_path, monkeypatch, capsys):
    """Sanitized remote app plists mirror the host's agent/daemon shape."""
    system = tmp_path / "LaunchDaemons"
    user = tmp_path / "LaunchAgents"
    system.mkdir()
    user.mkdir()
    app = _write_app(tmp_path / "apps", "Remote Demo", "com.example.remote")
    for root, suffix, executable in ((system, "daemon", "RemoteDaemon"), (user, "agent", "RemoteService")):
        _write_plist(
            root / f"com.example.remote.{suffix}.plist",
            {
                "Label": f"com.example.remote.{suffix}",
                "KeepAlive": True,
                "RunAtLoad": True,
                "AssociatedBundleIdentifiers": ["com.example.remote"],
                "ProgramArguments": [str(app / "Contents/MacOS" / executable), f"-{suffix}"],
            },
        )
    monkeypatch.setattr(
        "travel_mode.system.daemon_discovery.read_command",
        lambda command, timeout=30: str(app / "Contents/MacOS/RemoteDemo"),
    )
    rows, warnings = discover_daemon_apps(plist_dirs=(system, user), app_dirs=(tmp_path / "apps",))
    assert not warnings
    assert len(rows) == 1 and rows[0]["recommended"] and rows[0]["confidence"] == "path"
    assert all(match["recommended"] for match in rows[0]["matches"])
    assert len(rows[0]["system_daemons"]) == len(rows[0]["user_agents"]) == 1
    from travel_mode.paths import config_path

    config_path().write_text(
        '[apps]\n[[apps.with_daemon]]\nname="Remote Demo"\n'
        'system_daemons=[]\nuser_agents=[]\ngroup="network"\n'
    )
    monkeypatch.setattr(suggest_module, "read_command", lambda command, timeout=30: "")
    monkeypatch.setattr(suggest_module, "svc_scan", lambda config: {"candidates": []})
    monkeypatch.setattr(suggest_module, "discover_daemon_apps", lambda **kwargs: (rows, []))
    data = suggest_data(1)
    assert data["daemon_apps"][0]["configured"] is True
    assert not data["with_daemon"] and not data["toml"]
    suggest_module.print_suggestions(data)
    assert "已经配置：Remote Demo" in capsys.readouterr().out
    config_path().write_text("")
    data = suggest_data(1)
    assert data["daemon_apps"][0]["configured"] is False
    assert data["with_daemon"][0]["name"] == "Remote Demo"
    assert 'name = "Remote Demo"' in data["toml"]

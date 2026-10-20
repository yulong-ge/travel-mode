"""Legacy migration tests isolated from the real home directory."""

from __future__ import annotations

import json

from travel_mode import config
from travel_mode.config import load_config, migrate_legacy
from travel_mode.paths import config_path, legacy_conf_path, legacy_state_path, state_path
from travel_mode.state import load_state


def _isolate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.delenv("TRAVEL_MODE_CONFIG", raising=False)
    monkeypatch.delenv("TRAVEL_MODE_STATE", raising=False)
    return home


def test_legacy_config_migrates_to_equivalent_toml_and_is_renamed(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    old = legacy_conf_path()
    old.parent.mkdir(parents=True)
    old.write_text("default: freeze\nquit: Player|Editor\nPlayer=ask\n", encoding="utf-8")

    messages = migrate_legacy()

    new = config_path()
    assert new.exists()
    assert not old.exists()
    assert old.with_name(old.name + ".migrated").exists()
    migrated = load_config(new)
    assert migrated.apps.default == "freeze"
    assert migrated.apps.watch == ("Player", "Editor")
    assert migrated.apps.quit == ("Editor",)
    assert migrated.apps.ask == ("Player",)
    assert any("迁移旧配置" in message for message in messages)
    assert not list(home.glob("**/*real-home-touch*"))


def test_legacy_state_migrates_and_gets_current_version(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    old = legacy_state_path()
    old.parent.mkdir(parents=True)
    old.write_text('QUIT_APPS="/Applications/Player.app|/Applications/Player.app"\n', encoding="utf-8")

    messages = migrate_legacy()

    new = state_path()
    assert new.exists()
    assert not old.exists()
    assert old.with_name(old.name + ".migrated").exists()
    migrated = load_state(new)
    assert migrated["quit_apps"] == ["/Applications/Player.app"]
    from travel_mode.state import STATE_VERSION

    assert migrated["version"] == STATE_VERSION
    assert any("迁移旧状态" in message for message in messages)


def test_when_both_old_and_new_exist_migration_does_not_overwrite(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    old_conf = legacy_conf_path()
    old_conf.parent.mkdir(parents=True)
    old_conf.write_text("quit: Old\n", encoding="utf-8")
    new_conf = config_path()
    new_conf.parent.mkdir(parents=True)
    new_conf.write_text('[apps]\ndefault = "ask"\nquit = ["New"]\n', encoding="utf-8")

    old_state = legacy_state_path()
    old_state.write_text('QUIT_APPS="old"\n', encoding="utf-8")
    new_state = state_path()
    new_state.parent.mkdir(parents=True)
    new_state.write_text(json.dumps({"version": 2, "quit_apps": ["new"]}), encoding="utf-8")
    before_conf, before_state = new_conf.read_bytes(), new_state.read_bytes()

    messages = migrate_legacy()

    assert new_conf.read_bytes() == before_conf
    assert new_state.read_bytes() == before_state
    assert old_conf.exists() and old_state.exists()
    assert not old_conf.with_name(old_conf.name + ".migrated").exists()
    assert not old_state.with_name(old_state.name + ".migrated").exists()
    assert sum("新配置已存在" in message for message in messages) == 1
    assert sum("新状态已存在" in message for message in messages) == 1


def test_load_conf_group_single_default_and_comments(tmp_path, monkeypatch):
    path = tmp_path / "travel-mode.conf"
    path.write_text("""# comment\nfreeze: Alpha|Beta\nAlpha=quit\ndefault: ask\nblank = invalid\n""")
    monkeypatch.setattr(config, "CONF_PATH", path)
    actions, default = config.load_conf()
    assert actions == {"Alpha": "quit", "Beta": "freeze"}
    assert default == "ask"


def test_ensure_conf_writes_default(tmp_path, monkeypatch):
    path = tmp_path / "travel-mode.conf"
    monkeypatch.setattr(config, "CONF_PATH", path)
    assert config.ensure_conf() is True
    assert path.exists()
    assert config.ensure_conf() is False

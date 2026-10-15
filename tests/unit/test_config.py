"""Configuration schema and parser boundary tests."""

from __future__ import annotations

import builtins
import importlib
import sys
import types
from pathlib import Path

import pytest

import travel_mode.config as config_module
from travel_mode.config import Config, ConfigError, load_config


def test_defaults_are_portable_and_complete(tmp_path: Path):
    config = Config.defaults()
    assert config.apps.default == "ask"
    assert "WeChat" in config.apps.freeze
    assert "Spotify" in config.apps.quit
    assert config.network.apps
    assert config.services.unload == ()
    assert config.power_overrides == {}
    assert config.options.language == "auto"
    assert load_config(tmp_path / "missing.toml") == config


def test_complete_valid_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """[apps]
default = "freeze"
watch = ["Browser"]
ignore = ["Ignored"]
quit = ["Player"]
freeze = ["Editor"]
skip = ["Indexer"]
ask = ["Browser"]
[[apps.with_daemon]]
name = "Remote"
system_daemons = ["/tmp/remote.daemon.plist"]
user_agents = ["/tmp/remote.agent.plist"]
group = "apps"
[network]
apps = ["Tailscale"]
[services]
unload = ["example.service"]
keep = ["example.keep"]
[power.battery]
displaysleep = 3
lowpowermode = "1"
[options]
aggressive = true
language = "zh"
[profiles.flight]
aggressive = true
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.apps.default == "freeze"
    assert config.apps.watch == ("Browser",)
    assert config.apps.with_daemon[0].group == "apps"
    assert config.services.unload == ("example.service",)
    assert config.power_overrides == {"displaysleep": "3", "lowpowermode": "1"}
    assert config.options.aggressive is True
    assert config.profiles["flight"].values == {"aggressive": True}


@pytest.mark.parametrize(
    ("content", "key"),
    [
        ("[unknown]\nvalue = 1\n", "unknown"),
        ('[apps]\nwatc = ["A"]\n', "apps.watc"),
        ('[apps]\ndefault = "invalid"\n', "apps.default"),
        ("[apps]\nfreeze = [1]\n", "apps.freeze[0]"),
        ("[power.battery]\nnot_a_power_key = 1\n", "power.battery.not_a_power_key"),
        ('[apps]\nquit = ["A"]\nfreeze = ["A"]\n', "apps.freeze[0]"),
        ('profiles = "flight"\n', "profiles"),
    ],
)
def test_each_schema_error_contains_file_and_key_path(tmp_path: Path, content: str, key: str):
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_config(path)
    message = str(error.value)
    assert str(path) in message
    assert key in message


def test_toml_syntax_error_keeps_cause_and_location(tmp_path: Path):
    path = tmp_path / "bad.toml"
    path.write_text('[apps\nfreeze = ["A"]\n', encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert error.value.__cause__ is not None
    assert "line" in str(error.value).lower()


def test_tomli_compatibility_branch_is_imported_when_tomllib_is_unavailable(monkeypatch, tmp_path: Path):
    """Exercise the Python 3.10 import branch without changing the runtime."""
    original_import = builtins.__import__
    fake_tomli = types.ModuleType("tomli")
    fake_tomli.TOMLDecodeError = ValueError
    fake_tomli.load = lambda fh: {"apps": {"default": "ask"}}

    def import_without_tomllib(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tomllib":
            raise ModuleNotFoundError("mocked tomllib")
        if name == "tomli":
            return fake_tomli
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_tomllib)
    monkeypatch.delitem(sys.modules, "tomllib", raising=False)
    monkeypatch.setitem(sys.modules, "tomli", fake_tomli)
    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.tomllib is fake_tomli
        path = tmp_path / "config.toml"
        path.write_text("default = ignored by mocked parser\n", encoding="utf-8")
        assert reloaded.load_config(path).apps.default == "ask"
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)

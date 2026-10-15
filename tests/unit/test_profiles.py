"""Phase 6.1 profile parsing and precedence."""

from __future__ import annotations

import pytest

from travel_mode.config import ConfigError, load_config
from travel_mode.profiles import builtin_profiles, profile_scope


def test_builtin_profiles_are_available():
    profiles = builtin_profiles()
    assert set(profiles) == {"flight", "overnight", "cafe"}
    assert profiles["flight"].values["power"]["battery"]["hibernatemode"] == 25


def test_profile_merge_user_then_cli_precedence(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[options]\naggressive = false\n[profiles.flight]\naggressive = false\n"
        "[profiles.cafe.power.battery]\ndisplaysleep = 1\n",
        "utf-8",
    )
    config = load_config(path)
    with profile_scope(config, "flight", aggressive=True) as active:
        assert active.options.aggressive is True
    with profile_scope(config, "cafe") as active:
        assert active.power_overrides["displaysleep"] == "1"


def test_unknown_profile_fails_explicitly(tmp_path):
    config = load_config(tmp_path / "missing.toml")
    with pytest.raises(ConfigError, match="missing"), profile_scope(config, "missing"):
        pass


@pytest.mark.parametrize(
    "body",
    [
        "[profiles.flight]\nunknown = true\n",
        "[profiles.flight]\nbrightness = 2\n",
        "[profiles.flight.power.battery]\nunknown_key = 1\n",
        '[profiles.flight]\nwifi = "maybe"\n',
    ],
)
def test_profile_schema_rejects_invalid_values(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body, "utf-8")
    with pytest.raises(ConfigError):
        load_config(path)

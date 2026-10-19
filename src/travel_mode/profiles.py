"""配置档校验、合并与单次调用的配置作用域。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace

from .config import Config, ConfigError, Profile
from .constants import POWER_KEY_ORDER
from .i18n import t

_active: ContextVar[tuple[Config, str] | None] = ContextVar("travel_profile", default=None)


def builtin_profiles() -> dict[str, Profile]:
    return {
        "flight": Profile(
            {
                "description": t("profiles.flight"),
                "scenario": "away",
                "aggressive": True,
                "power": {"battery": {"hibernatemode": 25, "displaysleep": 2}},
                "apps": {"default": "quit"},
                "network": {"disconnect": False},
                "wifi": "keep",
                "bluetooth": "keep",
                "spotlight": "off",
                "brightness": 0.15,
            }
        ),
        "overnight": Profile(
            {
                "description": t("profiles.overnight"),
                "scenario": "away",
                "power": {"battery": {"tcpkeepalive": 1, "powernap": 0, "standby": 1}},
            }
        ),
        "work": Profile(
            {
                "description": t("profiles.work"),
                "scenario": "work",
                "power": {"battery": {"lowpowermode": 1, "powernap": 0, "womp": 0, "tcpkeepalive": 1}},
                "wifi": "keep",
                "bluetooth": "keep",
            }
        ),
    }


def _merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = _merge(result.get(key, {}), value) if isinstance(value, dict) else value
    return result


def parse_profiles(raw: dict, path) -> dict[str, Profile]:
    profiles = builtin_profiles()
    for name, value in raw.items():
        prefix = f"profiles.{name}"

        def invalid(key):
            raise ConfigError(path, key, t("profiles.invalid_value"))

        if not isinstance(value, dict):
            invalid(prefix)
        allowed = {
            "description",
            "aggressive",
            "power",
            "apps",
            "network",
            "wifi",
            "bluetooth",
            "spotlight",
            "brightness",
            "scenario",
        }
        for key, item in value.items():
            location = f"{prefix}.{key}"
            if key not in allowed:
                invalid(location)
            if key == "description" and not isinstance(item, str):
                invalid(location)
            if key == "scenario" and item not in ("work", "away"):
                invalid(location)
            if key == "aggressive" and not isinstance(item, bool):
                invalid(location)
            if key in {"wifi", "bluetooth", "spotlight"} and item not in ("off", "keep"):
                invalid(location)
            if key == "brightness" and (
                isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 <= item <= 1
            ):
                invalid(location)
            if key in {"apps", "network", "power"}:
                subkey = {"apps": "default", "network": "disconnect", "power": "battery"}[key]
                if not isinstance(item, dict) or set(item) - {subkey}:
                    invalid(location)
                sub = item.get(subkey, {} if key == "power" else None)
                if key == "apps" and sub not in ("quit", "freeze", "skip", "ask"):
                    invalid(f"{location}.{subkey}")
                if key == "network" and not isinstance(sub, bool):
                    invalid(f"{location}.{subkey}")
                if key == "power":
                    if not isinstance(sub, dict):
                        invalid(f"{location}.{subkey}")
                    for power_key, number in sub.items():
                        if (
                            power_key not in POWER_KEY_ORDER
                            or isinstance(number, bool)
                            or not isinstance(number, (str, int, float))
                        ):
                            invalid(f"{location}.battery.{power_key}")
        # Keep user TOML values intact for round-trip introspection; merge with
        # the built-in values only when a profile is activated.
        profiles[name] = Profile(value)
    return profiles


def active_config() -> Config | None:
    current = _active.get()
    return current[0] if current else None


def active_name() -> str:
    current = _active.get()
    return current[1] if current else ""


def active_values() -> dict:
    config = active_config()
    return config.profiles[active_name()].values if config and active_name() else {}


def active_scenario() -> str:
    from .config import load_config

    return load_config().options.scenario


@contextmanager
def profile_scope(config: Config, name: str | None, *, aggressive=False, scenario: str | None = None):
    if name is not None and name not in config.profiles:
        raise ConfigError("<cli>", "--profile", t("profiles.unknown", name=name))
    if scenario not in (None, "ask", "work", "away"):
        raise ConfigError("<cli>", "--scenario", t("scenario.invalid"))
    selected = scenario or config.options.scenario
    if name is None and selected == "work":
        name = "work"
    values = (
        _merge(builtin_profiles().get(name, Profile()).values, config.profiles.get(name, Profile()).values)
        if name
        else {}
    )
    profile_scenario = values.get("scenario", "away" if name else selected)
    if scenario in ("work", "away") and name and profile_scenario != scenario:
        raise ConfigError("<cli>", "--scenario/--profile", t("scenario.conflict"))
    effective = replace(
        config,
        profiles={**config.profiles, **({name: Profile(values)} if name else {})},
        apps=replace(config.apps, default=values.get("apps", {}).get("default", config.apps.default)),
        power_overrides={
            **config.power_overrides,
            **{key: str(value) for key, value in values.get("power", {}).get("battery", {}).items()},
        },
        options=replace(
            config.options,
            aggressive=aggressive or values.get("aggressive", config.options.aggressive),
            scenario=profile_scenario,
        ),
    )
    token = _active.set((effective, name or ""))
    try:
        yield effective
    finally:
        _active.reset(token)

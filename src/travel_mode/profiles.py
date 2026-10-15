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
                "aggressive": True,
                "power": {"battery": {"hibernatemode": 25, "displaysleep": 2}},
                "apps": {"default": "quit"},
                "network": {"disconnect": True},
                "wifi": "off",
                "bluetooth": "off",
                "spotlight": "off",
                "brightness": 0.15,
            }
        ),
        "overnight": Profile(
            {
                "description": t("profiles.overnight"),
                "power": {"battery": {"tcpkeepalive": 1, "powernap": 0, "standby": 1}},
            }
        ),
        "cafe": Profile(
            {
                "description": t("profiles.cafe"),
                "power": {"battery": {"displaysleep": 3, "lowpowermode": 1}},
                "wifi": "keep",
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
        }
        for key, item in value.items():
            location = f"{prefix}.{key}"
            if key not in allowed:
                invalid(location)
            if key == "description" and not isinstance(item, str):
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


@contextmanager
def profile_scope(config: Config, name: str | None, *, aggressive=False):
    if name is not None and name not in config.profiles:
        raise ConfigError("<cli>", "--profile", t("profiles.unknown", name=name))
    values = (
        _merge(builtin_profiles().get(name, Profile()).values, config.profiles.get(name, Profile()).values)
        if name
        else {}
    )
    effective = replace(
        config,
        apps=replace(config.apps, default=values.get("apps", {}).get("default", config.apps.default)),
        power_overrides={
            **config.power_overrides,
            **{key: str(value) for key, value in values.get("power", {}).get("battery", {}).items()},
        },
        options=replace(
            config.options,
            aggressive=aggressive or values.get("aggressive", config.options.aggressive),
        ),
    )
    token = _active.set((effective, name or ""))
    try:
        yield effective
    finally:
        _active.reset(token)

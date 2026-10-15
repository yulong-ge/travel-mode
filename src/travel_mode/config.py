"""Strict TOML configuration and legacy migration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

from .i18n import t

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from .constants import (
    DEFAULT_ACTIONS,
    DEFAULT_APPS,
    DEFAULT_NET_APPS,
    DEFAULT_SERVICES_KEEP,
    DEFAULT_SERVICES_UNLOAD,
    POWER_KEY_ORDER,
)
from .paths import config_path, legacy_conf_path


class ConfigError(ValueError):
    def __init__(self, path: str | Path, key: str, message: str, *, cause=None):
        self.path, self.key, self.message = str(path), key, message
        super().__init__(f"{self.path}: {self.key}: {message}")
        if cause is not None:
            self.__cause__ = cause


def write_language(language: str, path: Path | None = None) -> None:
    """原子保存语言，保留已有 TOML 注释，失败时保留原文件。"""
    target = path or config_path()
    if language not in {"en", "zh"}:
        raise ConfigError(target, "options.language", t("config.must_be_auto_zh_or_en"))
    load_config(target)
    doc = tomlkit.parse(target.read_text("utf-8")) if target.exists() else tomlkit.document()
    if "options" not in doc:
        doc["options"] = tomlkit.table()
    doc["options"]["language"] = language
    target.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".language-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_text(tomlkit.dumps(doc), "utf-8")
        load_config(temporary)
        temporary.replace(target)
    except OSError as exc:
        raise ConfigError(target, "options.language", str(exc), cause=exc) from exc
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class DaemonApp:
    name: str
    system_daemons: tuple[str, ...] = ()
    user_agents: tuple[str, ...] = ()
    group: str = "network"


@dataclass(frozen=True)
class AppsConfig:
    default: str = "ask"
    watch: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    quit: tuple[str, ...] = ()
    freeze: tuple[str, ...] = ()
    skip: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    with_daemon: tuple[DaemonApp, ...] = ()

    @property
    def actions(self) -> dict[str, str]:
        return {
            name: action for action in ("quit", "freeze", "skip", "ask") for name in getattr(self, action)
        }

    @property
    def all(self) -> tuple[str, ...]:
        names = list(DEFAULT_APPS) + list(self.watch)
        for action in ("quit", "freeze", "skip", "ask"):
            names.extend(getattr(self, action))
        names.extend(item.name for item in self.with_daemon)
        return tuple(dict.fromkeys(name for name in names if name not in self.ignore))


@dataclass(frozen=True)
class NetworkConfig:
    apps: tuple[str, ...] = tuple(DEFAULT_NET_APPS)


@dataclass(frozen=True)
class ServicesConfig:
    unload: tuple[str, ...] = tuple(DEFAULT_SERVICES_UNLOAD)
    keep: tuple[str, ...] = tuple(DEFAULT_SERVICES_KEEP)


@dataclass(frozen=True)
class OptionsConfig:
    aggressive: bool = False
    language: str = "auto"


@dataclass(frozen=True)
class Profile:
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    apps: AppsConfig = field(default_factory=AppsConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    services: ServicesConfig = field(default_factory=ServicesConfig)
    power_overrides: dict[str, str] = field(default_factory=dict)
    options: OptionsConfig = field(default_factory=OptionsConfig)
    profiles: dict[str, Profile] = field(default_factory=dict)

    @classmethod
    def defaults(cls) -> Config:
        from .profiles import builtin_profiles

        return cls(
            apps=AppsConfig(**{key: tuple(value) for key, value in DEFAULT_ACTIONS.items()}),
            profiles=builtin_profiles(),
        )


_TOP_KEYS = {"apps", "network", "services", "power", "options", "profiles"}
_APP_KEYS = {"default", "watch", "ignore", "quit", "freeze", "skip", "ask", "with_daemon"}
_ACTIONS = ("quit", "freeze", "skip", "ask")


def _error(path: Path, key: str, message: str) -> ConfigError:
    return ConfigError(path, key, message)


def _table(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise _error(path, key, t("config.expected_a_table_got", v0=type(value).__name__))
    return value


def _strings(value: Any, path: Path, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _error(path, key, t("config.expected_an_array_of_strings_got", v0=type(value).__name__))
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise _error(path, f"{key}[{index}]", t("config.expected_a_string_got", v0=type(item).__name__))
        if not item.strip():
            raise _error(path, f"{key}[{index}]", t("config.expected_nonempty_string"))
        result.append(item)
    return tuple(result)


def _check_keys(data: dict[str, Any], allowed: set[str], path: Path, prefix: str) -> None:
    for key in data:
        if key not in allowed:
            raise _error(path, f"{prefix}.{key}" if prefix else key, t("config.unknown_key"))


def _parse_daemons(value: Any, path: Path) -> tuple[DaemonApp, ...]:
    if not isinstance(value, list):
        raise _error(
            path, "apps.with_daemon", t("config.expected_an_array_of_tables_got", v0=type(value).__name__)
        )
    result = []
    for index, item in enumerate(value):
        key = f"apps.with_daemon[{index}]"
        if not isinstance(item, dict):
            raise _error(path, key, t("config.expected_a_table_got", v0=type(item).__name__))
        _check_keys(item, {"name", "system_daemons", "user_agents", "group"}, path, key)
        if not isinstance(item.get("name"), str):
            raise _error(path, f"{key}.name", t("config.expected_a_string"))
        if not item["name"].strip():
            raise _error(path, f"{key}.name", t("config.expected_nonempty_string"))
        group = item.get("group", "network")
        if group not in {"network", "apps"}:
            raise _error(path, f"{key}.group", t("config.must_be_network_or_apps"))
        result.append(
            DaemonApp(
                item["name"],
                _strings(item.get("system_daemons", []), path, f"{key}.system_daemons"),
                _strings(item.get("user_agents", []), path, f"{key}.user_agents"),
                group,
            )
        )
    return tuple(result)


def load_config(path: str | Path | None = None) -> Config:
    from .profiles import active_config, parse_profiles

    if path is None and active_config() is not None:
        return active_config()
    target = Path(path).expanduser() if path is not None else config_path()
    if not target.exists():
        return Config.defaults()
    try:
        with target.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(target, "<toml>", str(exc), cause=exc) from exc
    if not isinstance(data, dict):
        raise _error(target, "<root>", t("config.expected_a_toml_table"))
    _check_keys(data, _TOP_KEYS, target, "")

    apps = _table(data, "apps", target)
    _check_keys(apps, _APP_KEYS, target, "apps")
    default = apps.get("default", "ask")
    if default not in _ACTIONS:
        raise _error(target, "apps.default", t("config.invalid_action"))
    actions = {action: _strings(apps.get(action, []), target, f"apps.{action}") for action in _ACTIONS}
    seen: dict[str, str] = {}
    for action, names in actions.items():
        for index, name in enumerate(names):
            if name in seen:
                raise _error(
                    target, f"apps.{action}[{index}]", t("config.app_also_appears_in_apps", v0=seen[name])
                )
            seen[name] = action
    app_config = AppsConfig(
        default=default,
        watch=_strings(apps.get("watch", []), target, "apps.watch"),
        ignore=_strings(apps.get("ignore", []), target, "apps.ignore"),
        with_daemon=_parse_daemons(apps.get("with_daemon", []), target),
        **actions,
    )

    network = _table(data, "network", target)
    _check_keys(network, {"apps"}, target, "network")
    configured_network = _strings(network.get("apps", []), target, "network.apps")
    # Built-ins are defaults; the runtime still consumes this resolved config field.
    network_config = NetworkConfig(tuple(dict.fromkeys((*DEFAULT_NET_APPS, *configured_network))))
    services = _table(data, "services", target)
    _check_keys(services, {"unload", "keep"}, target, "services")
    service_config = ServicesConfig(
        _strings(services.get("unload", []), target, "services.unload"),
        _strings(services.get("keep", []), target, "services.keep"),
    )

    power = _table(data, "power", target)
    _check_keys(power, {"battery"}, target, "power")
    battery = _table(power, "battery", target)
    overrides: dict[str, str] = {}
    for key, value in battery.items():
        if key not in POWER_KEY_ORDER:
            raise _error(target, f"power.battery.{key}", t("config.not_a_supported_power_key"))
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise _error(target, f"power.battery.{key}", t("config.expected_a_string_or_number"))
        overrides[key] = str(value)

    options = _table(data, "options", target)
    _check_keys(options, {"aggressive", "language"}, target, "options")
    aggressive, language = options.get("aggressive", False), options.get("language", "auto")
    if not isinstance(aggressive, bool):
        raise _error(target, "options.aggressive", t("config.expected_a_boolean"))
    if language not in {"auto", "zh", "en"}:
        raise _error(target, "options.language", t("config.must_be_auto_zh_or_en"))

    raw_profiles = data.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise _error(target, "profiles", t("config.expected_a_table"))
    profiles = parse_profiles(raw_profiles, target)
    return Config(
        apps=app_config,
        network=network_config,
        services=service_config,
        power_overrides=overrides,
        options=OptionsConfig(aggressive, language),
        profiles=profiles,
    )


# Legacy parser retained for migration and phase-1 callers.
CONF_PATH = legacy_conf_path()
LEGACY_CONF_TEMPLATE = "# legacy compatibility file\ndefault: ask\n"


def load_conf(path: str | Path | None = None):
    target = Path(path).expanduser() if path is not None else Path(CONF_PATH)
    if not target.exists():
        return {}, ""
    actions, default = {}, ""
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("default:"):
            value = line.split(":", 1)[1].strip()
            if value in _ACTIONS:
                default = value
        elif ":" in line:
            action, rest = line.split(":", 1)
            action = action.strip()
            if action in _ACTIONS:
                for name in rest.split("|"):
                    if name.strip():
                        actions[name.strip()] = action
        elif "=" in line:
            name, action = (part.strip() for part in line.split("=", 1))
            if name and action in _ACTIONS:
                actions[name] = action
    return actions, default


def ensure_conf() -> bool:
    target = Path(CONF_PATH)
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(LEGACY_CONF_TEMPLATE, encoding="utf-8")
    return True


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_migrated_config(target: Path, actions: dict[str, str], default: str) -> None:
    names = list(actions)
    lines = ["# Migrated from legacy configuration", "[apps]", f"default = {_toml_string(default or 'ask')}"]
    lines.append("watch = [" + ", ".join(_toml_string(name) for name in names) + "]")
    lines.append("ignore = []")
    for action in ("quit", "freeze", "skip", "ask"):
        values = [name for name, value in actions.items() if value == action]
        lines.append(f"{action} = [" + ", ".join(_toml_string(name) for name in values) + "]")
    lines.extend(
        [
            "",
            "[network]",
            "apps = []",
            "",
            "[services]",
            "unload = []",
            "keep = []",
            "",
            "[power.battery]",
            "",
            "[options]",
            "aggressive = false",
            'language = "auto"',
            "",
        ]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")


def migrate_legacy(config_path_value=None, state_path_value=None) -> list[str]:
    from .paths import legacy_state_path, state_path
    from .state import load_state, save_state

    target = Path(config_path_value).expanduser() if config_path_value is not None else config_path()
    state_target = Path(state_path_value).expanduser() if state_path_value is not None else state_path()
    messages = []
    old_conf = legacy_conf_path()
    if target.exists() and old_conf.exists():
        messages.append(t("config.new_configuration_exists_at_legacy_configuration", v0=target))
    elif not target.exists() and old_conf.exists():
        actions, default = load_conf(old_conf)
        _write_migrated_config(target, actions, default)
        old_conf.rename(old_conf.with_name(old_conf.name + ".migrated"))
        messages.append(t("config.migrated_legacy_configuration_to", v0=target))
    old_state = legacy_state_path()
    if state_target.exists() and old_state.exists():
        messages.append(t("config.new_state_exists_at_legacy_state", v0=state_target))
    elif not state_target.exists() and old_state.exists():
        loaded = load_state(path=old_state)
        if loaded is None:
            raise ValueError(t("config.cannot_migrate_legacy_state_no_recognizable", v0=old_state))
        save_state(loaded, path=state_target)
        old_state.rename(old_state.with_name(old_state.name + ".migrated"))
        messages.append(t("config.migrated_legacy_state_to", v0=state_target))
    return messages


__all__ = [
    "CONF_PATH",
    "AppsConfig",
    "Config",
    "ConfigError",
    "DaemonApp",
    "NetworkConfig",
    "OptionsConfig",
    "Profile",
    "ServicesConfig",
    "ensure_conf",
    "load_conf",
    "load_config",
    "migrate_legacy",
]

"""Explicit language selection and packaged, flat message catalogs."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from importlib.resources import files

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

# Library callers retain the project's historical Chinese console default. The
# CLI always resolves this value from --lang, the environment, and config.
_language = "zh"


def select_language(cli: str | None = None, config: str = "auto") -> str:
    """Resolve CLI > environment > config > process locale, without hiding errors."""
    for value in (cli, os.environ.get("TRAVEL_MODE_LANG"), config):
        if value is None or value == "auto":
            continue
        if value not in {"en", "zh"}:
            raise ValueError(t("i18n.invalid_language", value=value))
        return value
    locale = next((os.environ[key] for key in ("LC_ALL", "LC_MESSAGES", "LANG") if os.environ.get(key)), "")
    return "zh" if locale.lower().startswith("zh") else "en"


def set_language(language: str) -> None:
    global _language
    if language not in {"en", "zh"}:
        raise ValueError(t("i18n.invalid_language", value=language))
    _language = language


def get_language() -> str:
    return _language


@lru_cache(maxsize=2)
def catalog(language: str) -> dict[str, str]:
    if language not in {"en", "zh"}:
        raise ValueError(t("i18n.invalid_language", value=language))
    data = tomllib.loads(files("travel_mode").joinpath(f"locales/{language}.toml").read_text("utf-8"))
    if not all(isinstance(value, str) for value in data.values()):
        raise TypeError("Message catalogs must contain flat string values")
    return data


def t(key: str, **kwargs) -> str:
    return catalog(_language)[key].format(**kwargs)

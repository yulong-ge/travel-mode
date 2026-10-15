"""travel-mode package metadata."""

from importlib.metadata import PackageNotFoundError, version

from .i18n import t

try:
    __version__ = version("travel-mode")
except PackageNotFoundError as exc:
    raise PackageNotFoundError(t("init__.travel_mode_installation_metadata_was_not")) from exc

__all__ = ["__version__"]

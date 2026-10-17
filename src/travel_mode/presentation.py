"""Action wording follows execution mode; observation wording stays literal."""

from .i18n import catalog, get_language, t
from .system.shell import is_dry_run


def yes_no(value: bool) -> str:
    """Human-readable flags; never change structured data or exception details."""
    if type(value) is not bool:
        raise TypeError("yes_no requires a boolean")
    return t("value.yes" if value else "value.no")


def action_message(key: str, **kwargs) -> str:
    planned = f"plan.{key}"
    if is_dry_run() and planned in catalog(get_language()):
        return t(planned, **kwargs)
    return t(key, **kwargs)

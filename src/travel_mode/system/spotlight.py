"""System adapter: spotlight."""

from __future__ import annotations

from ..i18n import t
from .shell import ReadError, _read


def spotlight_on():
    out = _read(["mdutil", "-s", "/"])
    if "Indexing enabled" in out:
        return True
    if "Indexing disabled" in out:
        return False
    raise ReadError(t("safety.invalid_status", command="mdutil -s /", detail=out.strip()))

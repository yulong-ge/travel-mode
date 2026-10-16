"""Atomic evidence publication and strict telemetry readers for finite harnesses."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


class EvidenceError(ValueError):
    """Published evidence is malformed or does not satisfy its schema."""


@contextmanager
def atomic_output(path):
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=path.suffix, dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        yield temporary
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_text(path, text):
    with atomic_output(path) as temporary:
        temporary.write_text(text, encoding="utf-8")


def write_json(path, value):
    # Serialize before touching the destination; reject non-JSON numerical values.
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_json(path, *, required=()):
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"{path}: cannot read JSON evidence: {error}") from error
    if required:
        if not isinstance(value, dict):
            raise EvidenceError(f"{path}: expected a JSON object")
        missing = sorted(set(required) - value.keys())
        if missing:
            raise EvidenceError(f"{path}: missing required keys: {', '.join(missing)}")
        if isinstance(required, dict):
            for key, kind in required.items():
                if type(value[key]) is not kind:
                    raise EvidenceError(f"{path}: {key}: invalid type {type(value[key]).__name__}")
    return value


def acknowledged(path):
    """Absence before the first atomic publication is the only pending-file state."""
    path = Path(path)
    if not path.exists():
        return False
    value = read_json(path, required={"status": str})
    if value["status"] != "PASS":
        raise EvidenceError(f"{path}: acknowledgement status is {value['status']!r}")
    return True


LAYOUT_FIELDS = {
    "schema_version": int,
    "active": str,
    "widgets": list,
    "issues": list,
    "ready": bool,
    "pages_ready": list,
    "refresh_complete": bool,
    "body_visible": bool,
    "content_visible": bool,
    "action_generation": int,
    "mutation_busy": bool,
    "focused_id": (str, type(None)),
}


def validate_layout(value, path):
    if not isinstance(value, dict):
        raise EvidenceError(f"{path}: expected a layout object")
    missing = sorted(LAYOUT_FIELDS.keys() - value.keys())
    if missing:
        raise EvidenceError(f"{path}: missing required layout keys: {', '.join(missing)}")
    for key, kind in LAYOUT_FIELDS.items():
        kinds = kind if isinstance(kind, tuple) else (kind,)
        if type(value[key]) not in kinds:
            raise EvidenceError(f"{path}: layout.{key}: invalid type {type(value[key]).__name__}")
    if value["schema_version"] != 1 or value["action_generation"] < 0:
        raise EvidenceError(f"{path}: unsupported schema_version or invalid action_generation")
    if not all(isinstance(page, str) for page in value["pages_ready"]):
        raise EvidenceError(f"{path}: layout.pages_ready: expected strings")
    for index, row in enumerate(value["widgets"]):
        if (
            not isinstance(row, dict)
            or not {"id", "type", "region", "value"} <= row.keys()
            or type(row["id"]) not in (str, type(None))
            or not isinstance(row["type"], str)
            or not isinstance(row["region"], list)
            or len(row["region"]) != 4
            or not all(type(number) is int for number in row["region"])
        ):
            raise EvidenceError(f"{path}: layout.widgets[{index}]: invalid widget record")
    for index, row in enumerate(value["issues"]):
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("kind", "detail")):
            raise EvidenceError(f"{path}: layout.issues[{index}]: invalid issue record")
    return value


def write_layout(path, value):
    write_json(path, validate_layout(value, path))


def read_layout(path):
    return validate_layout(read_json(path), path)

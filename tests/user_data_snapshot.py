"""Read-only inventory of travel-mode user data, also usable before/after pytest."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path


def snapshot_tree(root: Path) -> dict:
    """Record names, mtimes and hashes without creating even a missing root."""
    if not root.exists() and not root.is_symlink():
        return {"exists": False, "entries": {}}
    entries = {}

    def visit(path: Path) -> None:
        metadata = path.lstat()
        entry = {"mtime_ns": metadata.st_mtime_ns}
        if stat.S_ISLNK(metadata.st_mode):
            entry.update(kind="symlink", target=str(path.readlink()))
        elif stat.S_ISREG(metadata.st_mode):
            entry.update(kind="file", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        elif stat.S_ISDIR(metadata.st_mode):
            entry.update(kind="directory")
        else:
            raise ValueError(f"Unsupported entry in user data inventory: {path}")
        entries[path.relative_to(root).as_posix()] = entry
        if entry["kind"] == "directory":
            for child in sorted(path.iterdir()):
                visit(child)

    visit(root)
    return {"exists": True, "entries": entries}


def snapshot_user_data(home: Path) -> dict:
    return {
        "state": snapshot_tree(home / ".local/state/travel-mode"),
        "config": snapshot_tree(home / ".config/travel-mode"),
    }


def assert_unchanged(before: dict, after: dict) -> None:
    """Fail with entry names and metadata only; never expose file contents."""
    changes = []
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        changes.append(f"{name}: before={old!r}, after={new!r}")
    assert not changes, "Travel-mode user data changed during tests:\n" + "\n".join(changes)


if __name__ == "__main__":
    print(json.dumps(snapshot_user_data(Path.home()), indent=2, sort_keys=True))

"""Bounded, explicitly owned diagnostic directories; never prune by name alone."""

import fcntl
import os
import shutil
import tempfile
from pathlib import Path

DRY_RUN_PREFIX = "travel-mode-dry-run-"
MARKER = ".travel-mode-owned"
IDENTITY = "travel-mode:dry-run:v1\n"


def retained_dry_run_directory():
    parent = Path(tempfile.gettempdir())
    # Serialize creation/pruning across CLI processes, not just Python threads.
    with (parent / ".travel-mode-dry-run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        directory = Path(tempfile.mkdtemp(prefix=DRY_RUN_PREFIX, dir=parent))
        try:
            (directory / MARKER).write_text(IDENTITY, encoding="utf-8")
            owned = []
            for candidate in parent.glob(DRY_RUN_PREFIX + "*"):
                marker = candidate / MARKER
                if (
                    not candidate.is_symlink()
                    and candidate.is_dir()
                    and candidate.stat().st_uid == os.getuid()
                    and not marker.is_symlink()
                    and marker.is_file()
                    and marker.read_bytes() == IDENTITY.encode()
                ):
                    owned.append(candidate)
            owned.sort(key=lambda path: ((path / MARKER).stat().st_mtime_ns, path.name), reverse=True)
            for old in owned[5:]:
                shutil.rmtree(old)
        except BaseException:
            shutil.rmtree(directory)
            raise
        return directory

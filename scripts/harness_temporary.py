"""Disposable fixture roots, separate from persistent failure evidence."""

import shutil
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path

from harness_evidence import write_json

MARKER = ".travel-mode-fixture"
IDENTITY = "travel-mode:e2e:v1\n"
DIAGNOSTICS = (
    "state.json",
    "config.toml",
    "history.jsonl",
    "automation.json",
    "guard.json",
    "travel-mode.log",
    "travel-mode.log.1",
    "refusing-build.stdout",
    "refusing-build.stderr",
)


def preserve_diagnostics(root, output):
    for name in DIAGNOSTICS:
        source = root / name
        if source.is_file():
            destination = output / name
            shutil.copyfile(source, destination)
            destination.chmod(0o600)


@contextmanager
def fixture_directory(output, prefix="travel-mode-e2e-"):
    with tempfile.TemporaryDirectory(prefix=prefix, dir=output) as directory:
        root = Path(directory).resolve()
        (root / MARKER).write_text(IDENTITY, encoding="utf-8")
        try:
            yield root
        except BaseException as error:
            write_json(
                output / "fixture-error.json",
                {"error": f"{type(error).__name__}: {error}", "traceback": traceback.format_exc()},
            )
            raise
        finally:
            preserve_diagnostics(root, output)


def owned_fixture(root):
    marker = root / MARKER
    return (
        not root.is_symlink()
        and not marker.is_symlink()
        and marker.is_file()
        and marker.read_bytes() == IDENTITY.encode()
    )

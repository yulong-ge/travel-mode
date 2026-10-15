"""Check required package resources and keep private/build artifacts out of releases."""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

REQUIRED = {
    "travel_mode/cli.py",
    "travel_mode/tui/style.tcss",
    "travel_mode/config_template.toml",
    "travel_mode/locales/en.toml",
    "travel_mode/locales/zh.toml",
}
FORBIDDEN_PARTS = {
    ".git",
    ".venv",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
    ".claude",
    ".cortexkit",
}
PUBLIC_DOCS = {"docs/sudoers.md", "docs/releasing.md"}
MAX_SOURCE_FILE = 100 * 1024


def check_entries(entries: dict[str, int], *, kind: str) -> None:
    for name, size in entries.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or FORBIDDEN_PARTS.intersection(path.parts):
            raise ValueError(f"{kind}: forbidden path {name}")
        if path.suffix in {".pyc", ".pyo"}:
            raise ValueError(f"{kind}: bytecode must not ship: {name}")
        if name.startswith("docs/") and name not in PUBLIC_DOCS and not name.startswith("docs/json-schema/"):
            raise ValueError(f"{kind}: unexpected documentation artifact {name}")
        if (
            kind == "sdist"
            and size > MAX_SOURCE_FILE
            and not name.startswith("tests/characterization/golden/")
        ):
            raise ValueError(f"sdist: oversized file {name} ({size} bytes)")
    required = REQUIRED if kind == "wheel" else {f"src/{name}" for name in REQUIRED}
    missing = required - entries.keys()
    if missing:
        raise ValueError(f"{kind}: missing resources {sorted(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("dist"))
    args = parser.parse_args()
    wheels = sorted(args.directory.glob("*.whl"))
    sources = sorted(args.directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error(
            f"Expected one wheel and one sdist in {args.directory}; got {len(wheels)}, {len(sources)}"
        )
    with ZipFile(wheels[0]) as archive:
        wheel = {entry.filename: entry.file_size for entry in archive.infolist() if not entry.is_dir()}
    if any(not name.startswith(("travel_mode/", "travel_mode-")) for name in wheel):
        raise ValueError("wheel: only the package and its distribution metadata may ship")
    check_entries(wheel, kind="wheel")
    with tarfile.open(sources[0]) as archive:
        entries = archive.getmembers()
        roots = {PurePosixPath(entry.name).parts[0] for entry in entries}
        if len(roots) != 1:
            raise ValueError(f"sdist: expected one archive root, got {sorted(roots)}")
        if any(not (entry.isfile() or entry.isdir()) for entry in entries):
            raise ValueError("sdist: links and special files must not ship")
        source = {
            str(PurePosixPath(*PurePosixPath(entry.name).parts[1:])): entry.size
            for entry in entries
            if entry.isfile()
        }
    check_entries(source, kind="sdist")
    print(f"PASS: wheel ({len(wheel)} files), sdist ({len(source)} files); resources and artifact limits OK")


if __name__ == "__main__":
    main()

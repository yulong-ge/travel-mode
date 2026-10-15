#!/usr/bin/env -S uv run --script
"""Extract one Keep a Changelog heading, including its content."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def section(text: str, version: str) -> str:
    pattern = re.compile(rf"^## \[{re.escape(version)}\].*?$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        raise ValueError(f"CHANGELOG section not found: {version}")
    tail = text[match.end() :]
    next_heading = re.search(r"^## \[", tail, re.MULTILINE)
    return text[match.start() : match.end() + (next_heading.start() if next_heading else len(tail))].rstrip()


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: changelog_section.py VERSION")
    print(section(Path("CHANGELOG.md").read_text("utf-8"), args[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

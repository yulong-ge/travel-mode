"""Capture deterministic demo screenshots without reading the host app inventory."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from terminal_render import render as render
from tui_debug import TABS, Session

ROOT = Path(__file__).resolve().parents[1]
COLS, ROWS = 120, 36


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="required deterministic fixture mode")
    args = parser.parse_args()
    if not args.demo:
        raise SystemExit("Refusing to capture without --demo; screenshots must use FakeRunner fixtures")
    assets = ROOT / "docs" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for language, suffix in (("en", ""), ("zh", ".zh")):
        root = Path(tempfile.mkdtemp(prefix="travel-mode-assets-"))
        session = Session(root, COLS, ROWS, language, False)
        try:
            session.wait_text("SleepDisabled")
            for key, name in enumerate(TABS, 1):
                session.keys(str(key))
                session.capture(name, name)
                for extension in ("png", "txt", "ansi"):
                    shutil.copyfile(root / f"{name}.{extension}", assets / f"{name}{suffix}.{extension}")
        finally:
            session.close()
    print("Generated English and Chinese demo screenshots from FakeRunner fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Publish deterministic 120×36 demo screens without reading host inventories."""

from __future__ import annotations

import argparse
import re
import shutil
import tempfile
from pathlib import Path

from harness_evidence import write_text
from terminal_render import render as render
from tui_debug import Session

ROOT = Path(__file__).resolve().parents[1]
COLS, ROWS = 120, 36
SCREENS = ("plan", "result", "session")
LANGUAGES = (("en", ""), ("zh", ".zh"))
EXTENSIONS = ("png", "ansi", "txt")
LEGACY_SCREENS = ("dashboard", "apps", "power", "services", "network")
DEMO_LOG = re.compile(r"/tmp/travel-mode-demo-[a-z0-9_]+/travel-mode\.log")


def capture_language(root: Path, language: str) -> None:
    """Use committed layouts and synthetic outcomes; never execute Actions."""
    session = Session(root, COLS, ROWS, language, False)
    try:
        session.wait_started()
        session.wait_complete(active="scenario")
        session.keys("1", "Enter")
        session.wait_complete(active="plan", group="all")
        for name, keys in (("plan", ()), ("result", ("F6",)), ("session", ("F7",))):
            if keys:
                session.keys(*keys)
                session.wait_complete(active=name)
            capture = session.capture(name, name)
            if capture["issues"]:
                raise RuntimeError(f"{language}/{name}: screenshot issues: {capture['issues']}")
    finally:
        session.close()


def generate(assets: Path) -> list[Path]:
    """Stage both languages before replacing assets or removing obsolete ones."""
    with tempfile.TemporaryDirectory(prefix="travel-mode-assets-") as temporary:
        stage = Path(temporary)
        for language, suffix in LANGUAGES:
            root = stage / language
            root.mkdir()
            capture_language(root, language)
            for name in SCREENS:
                for extension in ("ansi", "txt"):
                    text = (root / f"{name}.{extension}").read_text(encoding="utf-8")
                    # The demo's isolated log directory is random. Publish a generic
                    # illustrative path; no file is read or written at that path.
                    text = DEMO_LOG.sub("/tmp/travel-mode-demo/travel-mode.log", text)
                    write_text(stage / f"{name}{suffix}.{extension}", text)
                render(
                    (stage / f"{name}{suffix}.ansi").read_text(encoding="utf-8"),
                    stage / f"{name}{suffix}.png",
                    COLS,
                    ROWS,
                )
        assets.mkdir(parents=True, exist_ok=True)
        generated = []
        for _, suffix in LANGUAGES:
            for name in SCREENS:
                for extension in EXTENSIONS:
                    target = assets / f"{name}{suffix}.{extension}"
                    shutil.copyfile(stage / target.name, target)
                    generated.append(target)
        for name in LEGACY_SCREENS:
            for _, suffix in LANGUAGES:
                for extension in EXTENSIONS:
                    (assets / f"{name}{suffix}.{extension}").unlink(missing_ok=True)
        return generated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="required deterministic fixture mode")
    args = parser.parse_args()
    if not args.demo:
        raise SystemExit("Refusing to capture without --demo; screenshots must use FakeRunner fixtures")
    for path in generate(ROOT / "docs" / "assets"):
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Release capture drives only fixture screens and replaces the legacy asset set."""

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def capture(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("release_capture", scripts / "tmux_capture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_session(capture, monkeypatch, *, issues=False):
    calls = []

    class Session:
        def __init__(self, root, cols, rows, language, live):
            assert (cols, rows, live) == (120, 36, False)
            self.root = root
            calls.append(("start", language))

        def wait_started(self):
            calls.append("started")

        def wait_complete(self, **kwargs):
            calls.append(("complete", kwargs))

        def keys(self, *keys):
            calls.append(("keys", keys))

        def capture(self, name, screen):
            assert name == screen
            calls.append(("capture", name))
            for extension in ("txt", "ansi"):
                (self.root / f"{name}.{extension}").write_text(
                    f"{name}: /tmp/travel-mode-demo-random123/travel-mode.log\n", encoding="utf-8"
                )
            return {"issues": ["invalid layout"] if issues else []}

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(capture, "Session", Session)
    return calls


def test_release_capture_uses_committed_new_screens_without_applying(capture, monkeypatch, tmp_path):
    calls = fake_session(capture, monkeypatch)
    capture.capture_language(tmp_path, "zh")
    assert calls == [
        ("start", "zh"),
        "started",
        ("complete", {"active": "scenario"}),
        ("keys", ("1", "Enter")),
        ("complete", {"active": "plan", "group": "all"}),
        ("capture", "plan"),
        ("keys", ("F6",)),
        ("complete", {"active": "result"}),
        ("capture", "result"),
        ("keys", ("F7",)),
        ("complete", {"active": "session"}),
        ("capture", "session"),
        "closed",
    ]


def test_release_assets_are_bilingual_deterministic_and_remove_only_legacy(capture, monkeypatch, tmp_path):
    fake_session(capture, monkeypatch)
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in capture.LEGACY_SCREENS:
        for suffix in ("", ".zh"):
            for extension in capture.EXTENSIONS:
                (assets / f"{name}{suffix}.{extension}").write_bytes(b"old")
    (assets / "unrelated.svg").write_bytes(b"keep")
    rendered = []

    def render(text, path, cols, rows):
        assert (cols, rows) == (120, 36)
        rendered.append(text)
        path.write_bytes(text.encode())

    monkeypatch.setattr(capture, "render", render)
    expected = {
        f"{name}{suffix}.{extension}"
        for name in ("plan", "result", "session")
        for suffix in ("", ".zh")
        for extension in ("png", "ansi", "txt")
    }
    assert {path.name for path in capture.generate(assets)} == expected
    first = {path.name: path.read_bytes() for path in assets.iterdir()}
    assert set(first) == expected | {"unrelated.svg"}
    assert all(b"random123" not in value for value in first.values())
    assert all("/tmp/travel-mode-demo/travel-mode.log" in text for text in rendered)
    capture.generate(assets)
    assert first == {path.name: path.read_bytes() for path in assets.iterdir()}


def test_invalid_capture_preserves_existing_assets_and_closes_session(capture, monkeypatch, tmp_path):
    calls = fake_session(capture, monkeypatch, issues=True)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "dashboard.png").write_bytes(b"old")
    with pytest.raises(RuntimeError, match="screenshot issues"):
        capture.generate(assets)
    assert calls[-1] == "closed"
    assert list(assets.iterdir()) == [assets / "dashboard.png"]
    assert (assets / "dashboard.png").read_bytes() == b"old"


def test_release_capture_requires_explicit_demo(capture, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tmux_capture.py"])
    monkeypatch.setattr(capture, "generate", lambda _: pytest.fail("capture started without --demo"))
    with pytest.raises(SystemExit, match="Refusing to capture without --demo"):
        capture.main()

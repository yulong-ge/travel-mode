"""Strict stateful doubles for the local harness; no host commands."""

import importlib.util
import sys
from pathlib import Path

import pytest

scripts = Path(__file__).resolve().parents[2] / "scripts"
spec = importlib.util.spec_from_file_location("e2e_local", scripts / "e2e_local.py")
e2e = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = e2e
spec.loader.exec_module(e2e)
from e2e_fake import Machine  # noqa: E402
from e2e_guard import GuardRunner  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, darwin_platform, monkeypatch):
    monkeypatch.setattr("travel_mode.system.brightness.select_backend", lambda: None)
    root = tmp_path / "travel-mode-e2e-test"
    root.mkdir()
    machine = Machine(root)
    guard = GuardRunner(machine, root, e2e.fixture_paths(root), root / "agent.plist")
    harness = e2e.Harness(root, guard, tmp_path / "user-state.json", pause=lambda _: None)
    return harness, guard, machine

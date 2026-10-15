"""Phase 6.6/6.7 release and integration artifacts."""

from __future__ import annotations

import runpy
from pathlib import Path

import yaml


def test_changelog_section_has_explicit_failure():
    section = runpy.run_path("scripts/changelog_section.py")["section"]
    assert section("## [1.0]\n\n- x\n## [0.9]\n", "1.0").startswith("## [1.0]")
    try:
        section("## [1.0]\n", "2.0")
    except ValueError as exc:
        assert "2.0" in str(exc)
    else:
        raise AssertionError("missing changelog version must fail")


def test_release_and_integration_artifacts_exist():
    workflow = Path(".github/workflows/release.yml").read_text("utf-8")
    yaml.safe_load(workflow)
    assert 'tags: ["v*"]' in workflow
    assert "gh-action-pypi-publish" in workflow
    formula = Path("packaging/homebrew/travel-mode.rb").read_text("utf-8")
    assert 'resource "textual"' in formula
    assert 'resource "tomlkit"' in formula
    assert Path("docs/releasing.md").exists()
    assert Path("docs/sudoers.md").exists()
    swiftbar = Path("integrations/swiftbar/travel-mode.10s.sh").read_text("utf-8")
    assert "check --json" in swiftbar
    assert "--profile" in swiftbar and 'param1="off"' in swiftbar
    assert Path("integrations/shortcuts.md").exists()

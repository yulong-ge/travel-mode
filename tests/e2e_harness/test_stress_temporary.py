"""Stress failures retain evidence only when an output root was requested."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import stress_tui


@pytest.mark.parametrize("keep", [False, True])
def test_stress_failed_child_cleans_default_root_or_keeps_explicit_output(tmp_path, monkeypatch, keep):
    roots = []

    def failed(command, *, env, stdout, **kwargs):
        root = Path(env["TMPDIR"]).parent.parent
        roots.append(root)
        assert root.exists()
        stdout.write("fixture child failure\n")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(stress_tui.shutil, "which", lambda _: "/fixture/uv")
    monkeypatch.setattr(stress_tui.subprocess, "run", failed)
    output = tmp_path / "evidence"
    argv = ["--runs", "1", "--jobs", "1", "tests/tui"]
    assert stress_tui.main(argv + (["--output", str(output)] if keep else [])) == 1
    assert len(roots) == 1 and roots[0].exists() is keep
    if keep:
        assert json.loads((output / "summary.json").read_text())["results"][0]["rc"] == 1
        assert (output / "run-001/pytest.log").read_text() == "fixture child failure\n"

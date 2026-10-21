"""Fixture deletion must not depend on constructor/driver/cleanup success."""

import json

import pytest
from harness_temporary import fixture_directory


@pytest.mark.parametrize("error", [None, RuntimeError("driver failed"), KeyboardInterrupt()])
def test_fixture_scope_preserves_evidence_but_removes_root(tmp_path, error):
    output = tmp_path / "output"
    output.mkdir()
    root = None

    def run():
        nonlocal root
        with fixture_directory(output) as root:
            (root / "state.json").write_text('{"pending": true}')
            (root / "travel-mode.log").write_text("fixture failure evidence")
            if error is not None:
                raise error

    if error is None:
        run()
    else:
        with pytest.raises(type(error)):
            run()
        diagnostic = json.loads((output / "fixture-error.json").read_text())
        assert diagnostic["error"].startswith(type(error).__name__)
    assert root is not None and not root.exists()
    assert json.loads((output / "state.json").read_text()) == {"pending": True}
    assert (output / "travel-mode.log").read_text() == "fixture failure evidence"


def test_ui_constructor_failure_removes_fixture_and_keeps_evidence(tmp_path, monkeypatch):
    import e2e_ui

    roots = []

    def broken(evidence, fixture, *args, **kwargs):
        roots.append(fixture)
        (fixture / "state.json").write_text("owned fixture state")
        raise RuntimeError("fixture constructor failed")

    monkeypatch.setattr(e2e_ui, "UISession", broken)
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="fixture constructor failed"):
        e2e_ui.main(["--demo", "--interface", "cli", "--output", str(output)])
    assert roots and all(not root.exists() for root in roots)
    assert (output / "work-cli/state.json").read_text() == "owned fixture state"
    assert "fixture constructor failed" in (output / "work-cli/fixture-error.json").read_text()


def test_local_constructor_failure_removes_fixture_root(tmp_path, monkeypatch):
    import e2e_local

    roots = []

    def broken(root, *args, **kwargs):
        roots.append(root)
        (root / "state.json").write_text("fixture state")
        raise RuntimeError("fixture harness failed")

    monkeypatch.setattr(e2e_local.pwd, "getpwuid", lambda _: type("User", (), {"pw_dir": str(tmp_path)})())
    monkeypatch.setattr(e2e_local, "Harness", broken)
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="fixture harness failed"):
        e2e_local.main(["--output", str(output)])
    assert roots and not roots[0].exists()
    assert (output / "state.json").read_text() == "fixture state"

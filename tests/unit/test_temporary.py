"""Temporary diagnostics are bounded without deleting unowned user directories."""

import os
from pathlib import Path

import pytest

from travel_mode import demo, state, temporary
from travel_mode.actions.runtime import command_lifecycle
from travel_mode.system import shell


def test_dry_run_retains_five_marked_roots_and_preserves_unowned_entries(tmp_path):
    parent = Path(os.environ["TMPDIR"])
    unmarked = parent / (temporary.DRY_RUN_PREFIX + "unmarked")
    unmarked.mkdir()
    invalid = parent / (temporary.DRY_RUN_PREFIX + "other-marker")
    invalid.mkdir()
    (invalid / temporary.MARKER).write_bytes(b"\xffother program")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / temporary.MARKER).write_text(temporary.IDENTITY)
    linked = parent / (temporary.DRY_RUN_PREFIX + "linked")
    linked.symlink_to(outside, target_is_directory=True)
    marker_link = parent / (temporary.DRY_RUN_PREFIX + "marker-link")
    marker_link.mkdir()
    (marker_link / temporary.MARKER).symlink_to(outside / temporary.MARKER)
    created = []
    try:
        for index in range(8):
            directory = shell.enable_dry_run()
            (directory / "state.json").write_text(str(index))
            shell.disable_dry_run()
            os.utime(directory / temporary.MARKER, ns=(index + 1, index + 1))
            created.append(directory)
        assert [path for path in created if path.exists()] == created[-5:]
        contents = [path.joinpath("state.json").read_text() for path in created[-5:]]
        assert contents == list(map(str, range(3, 8)))
        assert all(path.exists() for path in (unmarked, invalid, linked, marker_link, outside))
    finally:
        shell.disable_dry_run()


@pytest.mark.parametrize("fails", [False, True])
def test_demo_lifecycle_removes_root_and_restores_paths_on_success_or_failure(tmp_path, fails):
    observed = []
    original = shell.get_runner()

    def dispatch(argv):
        root = Path(os.environ["TRAVEL_MODE_STATE"]).parent
        demo._configure_demo()
        assert state._STATE_PATH_OVERRIDE.parent == root
        observed.append(root)
        if fails:
            raise RuntimeError("fixture demo failure")
        return 0

    if fails:
        with pytest.raises(RuntimeError, match="fixture demo failure"):
            command_lifecycle(["--demo"], dispatch)
    else:
        assert command_lifecycle(["--demo"], dispatch) == 0
    assert len(observed) == 1 and not observed[0].exists()
    assert shell.get_runner() is original
    assert os.environ["TRAVEL_MODE_STATE"] == str(tmp_path / "state.json")


@pytest.mark.parametrize("startup", [False, True])
def test_demo_script_scope_cleans_startup_and_ui_errors(tmp_path, monkeypatch, startup):
    original = demo._configure_demo
    roots = []

    def configure(root):
        roots.append(Path(root))
        original(root)
        if startup:
            raise RuntimeError("fixture demo failure")

    monkeypatch.setattr(demo, "_configure_demo", configure)
    with pytest.raises(RuntimeError, match="fixture demo failure"), demo.environment(tmp_path):
        raise RuntimeError("fixture demo failure")
    assert len(roots) == 1 and not roots[0].exists()

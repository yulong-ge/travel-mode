"""A smaller contract must still reject command/state regressions and bad updates."""

import copy
import json
import runpy
from pathlib import Path

import pytest

from . import conftest as contracts_fixture
from .contracts import ALIASES


def test_comparator_preserves_order_nested_state_and_exit_code(tmp_path, monkeypatch):
    golden = tmp_path / "golden"
    golden.mkdir()
    monkeypatch.setattr(contracts_fixture, "GOLDEN_DIR", golden)
    expected = dict(
        commands=[["kill", "-STOP", "42"], ["launchctl", "unload", "fixture.plist"]],
        state=dict(pending_commands=[["kill", "-STOP", "42"]], frozen_pids=[dict(pid=42)]),
        exit_code=0,
    )
    (golden / "fixture.json").write_text(json.dumps(expected))
    contracts_fixture.assert_golden("fixture", expected, tmp_path)
    changed = copy.deepcopy(expected)
    changed["commands"].reverse()
    with pytest.raises(AssertionError):
        contracts_fixture.assert_golden("fixture", changed, tmp_path)
    changed = copy.deepcopy(expected)
    changed["state"]["pending_commands"] = []
    with pytest.raises(AssertionError):
        contracts_fixture.assert_golden("fixture", changed, tmp_path)
    changed = {**expected, "exit_code": 1}
    with pytest.raises(AssertionError):
        contracts_fixture.assert_golden("fixture", changed, tmp_path)


def test_updater_rejects_incomplete_display_and_conflicting_aliases_before_any_write(tmp_path, monkeypatch):
    module = Path(__file__).with_name("update_golden.py")
    monkeypatch.syspath_prepend(str(module.parent))
    update = runpy.run_path(str(module))["update"]
    source, target = tmp_path / "actual", tmp_path / "golden"
    source.mkdir()
    target.mkdir()
    alias, canonical = next(iter(ALIASES.items()))
    expected = dict(commands=[], state=None, exit_code=0)
    original = json.dumps(expected)
    (target / f"{canonical}.json").write_text(original)
    (source / f"{canonical}.json").write_text(original)
    alias_path = source / f"{alias}.json"
    for invalid in ({"commands": []}, {**expected, "output": "display"}, {**expected, "state": []}):
        alias_path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError, match=str(alias_path)):
            update(source, target)
        assert (target / f"{canonical}.json").read_text() == original
    alias_path.write_text(json.dumps({**expected, "exit_code": 1}))
    with pytest.raises(ValueError, match="Conflicting aliases"):
        update(source, target)
    assert (target / f"{canonical}.json").read_text() == original
    alias_path.write_text(original)
    update(source, target)
    assert json.loads((target / f"{canonical}.json").read_text()) == expected
    (source / "unknown.json").write_text(original)
    with pytest.raises(ValueError, match="Unknown golden"):
        update(source, target)

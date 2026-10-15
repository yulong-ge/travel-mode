import pytest

from travel_mode.state import StateError, load_state


def test_nonempty_unparseable_state_is_an_error(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not a state file\n", encoding="utf-8")
    with pytest.raises(StateError, match="状态文件损坏"):
        load_state(path)


def test_higher_state_version_is_rejected(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 999}\n', encoding="utf-8")
    with pytest.raises(StateError, match="未知更高版本"):
        load_state(path)

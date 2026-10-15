from travel_mode import state


def test_state_json_roundtrip_deduplicates_and_anchors(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_PATH", path)
    value = state.new_state()
    value["quit_apps"] = ["/Applications/A.app", "/Applications/A.app"]
    value["saved_at"] = "2026-01-01 00:00:00"
    state.save_state(value)
    loaded = state.load_state()
    assert loaded["quit_apps"] == ["/Applications/A.app"]
    assert loaded["anchored_at"] == "2026-01-01 00:00:00"


def test_state_legacy_format(tmp_path, monkeypatch):
    path = tmp_path / "state"
    path.write_text('QUIT_APPS="a|b|a"\nSAVED_AT="2026-01-01 00:00:00"\n')
    monkeypatch.setattr(state, "STATE_PATH", path)
    loaded = state.load_state()
    assert loaded["quit_apps"] == ["a", "b", "a"]
    assert loaded["saved_at"] == "2026-01-01 00:00:00"

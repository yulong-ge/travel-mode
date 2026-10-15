from travel_mode import config


def test_load_conf_group_single_default_and_comments(tmp_path, monkeypatch):
    path = tmp_path / "travel-mode.conf"
    path.write_text("""# comment\nfreeze: Alpha|Beta\nAlpha=quit\ndefault: ask\nblank = invalid\n""")
    monkeypatch.setattr(config, "CONF_PATH", path)
    actions, default = config.load_conf()
    assert actions == {"Alpha": "quit", "Beta": "freeze"}
    assert default == "ask"


def test_ensure_conf_writes_default(tmp_path, monkeypatch):
    path = tmp_path / "travel-mode.conf"
    monkeypatch.setattr(config, "CONF_PATH", path)
    assert config.ensure_conf() is True
    assert path.exists()
    assert config.ensure_conf() is False

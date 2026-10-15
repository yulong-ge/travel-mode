from __future__ import annotations

import string
from pathlib import Path

import pytest

from travel_mode.i18n import catalog, select_language, set_language, t


def test_catalog_keys_and_placeholders_match():
    assert set(catalog("en")) == set(catalog("zh"))

    def fields(value):
        return {name for _, name, _, _ in string.Formatter().parse(value) if name is not None}

    for key in catalog("en"):
        assert fields(catalog("en")[key]) == fields(catalog("zh")[key]), key


def test_no_obsolete_services_tab_names_in_catalog_or_source():
    for language in ("en", "zh"):
        messages = catalog(language)
        assert "impl.agents" not in messages
        assert "{tab}" in messages["network.services_hint"]
        for value in messages.values():
            assert "Agents tab" not in value and "守护页" not in value
    root = Path(__file__).resolve().parents[2] / "src/travel_mode"
    for source in root.rglob("*.py"):
        text = source.read_text()
        assert 't("impl.agents")' not in text
        assert "Agents tab" not in text and "守护页" not in text


def test_language_priority(monkeypatch):
    monkeypatch.setenv("TRAVEL_MODE_LANG", "zh")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert select_language("en", "zh") == "en"
    assert select_language(None, "en") == "zh"
    monkeypatch.delenv("TRAVEL_MODE_LANG")
    assert select_language(None, "zh") == "zh"
    assert select_language(None, "auto") == "en"


def test_t_missing_key_is_explicit():
    set_language("en")
    with pytest.raises(KeyError):
        t("missing.message")
    set_language("zh")


@pytest.mark.parametrize(
    ("key", "kwargs"),
    [
        ("impl.processes_for_were_found_but_its", {"v0": "Demo", "v1": "12 Demo"}),
        ("impl.caffeinate_prevents_sleep_2", {"v0": "12 caffeinate"}),
        ("impl.battery_profile_after_update", {"v0": "sleep 5"}),
        ("impl.skipped_message", {"v0": "orphan process"}),
        ("impl.high_energy_apps_running", {"v0": 0, "v1": " (none)"}),
        ("impl.background_agents_unloadable", {"v0": 0, "v1": " (none)"}),
        ("impl.apps_done_quit_frozen", {"v0": 1, "v1": 2, "v2": " / pending"}),
        ("impl.batch_complete_quit_frozen", {"v0": 1, "v1": 2, "v2": " / pending"}),
        ("impl.final_audit_sleepdisabled", {"v0": "1", "v1": " still prevents sleep"}),
    ],
)
def test_i18n_messages_with_dynamic_suffixes_render_in_both_languages(key, kwargs):
    for language in ("en", "zh"):
        set_language(language)
        assert t(key, **kwargs)
    set_language("zh")

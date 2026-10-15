"""Phase 6.3 JSON output and published schema."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate

from travel_mode.flows.suggest import suggest_data
from travel_mode.structured import check_data, report_data


def _patch_check(monkeypatch):
    monkeypatch.setattr("travel_mode.system.apps.scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(
        "travel_mode.system.launchd.svc_scan",
        lambda config=None: {"unloadable": [], "candidates": [], "kept": [], "invalid": []},
    )
    monkeypatch.setattr("travel_mode.system.pmset.configured_sleep_blocking_assertions", lambda: [])


def test_check_json_conforms_to_schema(monkeypatch, tmp_paths, fake_runner):
    _patch_check(monkeypatch)
    data = check_data()
    assert json.dumps(data)
    assert data["battery"]["source"] == "battery"
    schema = json.loads(Path("docs/json-schema/check.schema.json").read_text("utf-8"))
    validate(data, schema)


def test_report_json_is_structured(monkeypatch):
    monkeypatch.setattr(
        "travel_mode.structured.read_command",
        lambda command, timeout=30: "2026-01-01 00:00:00 Using Batt, Charge: 80; Entering Sleep\n",
    )
    data = report_data()
    assert set(data) == {"version", "sleep_segments", "darkwake_reasons"}


def test_suggest_json_conforms_to_schema(monkeypatch):
    monkeypatch.setattr("travel_mode.flows.suggest.svc_scan", lambda config: {"candidates": []})
    monkeypatch.setattr("travel_mode.flows.suggest.discover_daemon_apps", lambda **kwargs: ([], []))
    monkeypatch.setattr("travel_mode.flows.suggest.read_command", lambda command, timeout=30: "")
    data = suggest_data(samples=1)
    schema = json.loads(Path("docs/json-schema/suggest.schema.json").read_text("utf-8"))
    validate(data, schema)

"""Evidence readers must never observe a published partial layout schema."""

import json
import os

import pytest
from harness_evidence import LAYOUT_FIELDS, EvidenceError, read_layout, write_json, write_layout, write_text
from tui_debug import DebugApp, Session


@pytest.mark.parametrize("scenario", [None, "away"])
async def test_every_layout_publication_has_action_fields_even_before_ready(tmp_path, monkeypatch, scenario):
    path = tmp_path / "layout.json"
    publications = []
    replace = os.replace

    def observe_commit(source, target, *args, **kwargs):
        result = replace(source, target, *args, **kwargs)
        if os.fspath(target) == str(path):
            # Deterministic reader at the exact publication boundary, before a
            # subclass can perform another write. No sleeps or timing retries.
            publications.append(json.loads(path.read_text()))
        return result

    monkeypatch.setattr(os, "replace", observe_commit)
    app = DebugApp(path, scenario=scenario)
    async with app.run_test(size=(120, 36)):
        await app.wait_for_idle()
        app.write_layout()
        app.refresh_complete = False
        app.write_layout()
    assert publications
    if scenario is not None:
        assert any(not layout["ready"] for layout in publications)
    for layout in publications:
        assert LAYOUT_FIELDS.keys() <= layout.keys()
        assert read_layout(path)["schema_version"] == 1


@pytest.mark.parametrize("raw", ['{"ready": true}', '{"ready":'])
def test_debug_reader_rejects_old_or_malformed_layout_with_path(tmp_path, monkeypatch, raw):
    path = tmp_path / "layout.json"
    path.write_text(raw)
    session = object.__new__(Session)
    session.root, session.timeout = tmp_path, 1
    monkeypatch.setattr("tui_debug.time.sleep", lambda _: pytest.fail("Invalid evidence must not be retried"))
    with pytest.raises(ValueError, match=r"layout\.json"):
        session.wait_complete()


def frame():
    return dict(
        schema_version=1,
        active="p-dash",
        widgets=[],
        issues=[],
        ready=True,
        pages_ready=["p-dash"],
        refresh_complete=True,
        body_visible=True,
        content_visible=True,
        action_generation=0,
        mutation_busy=False,
        focused_id=None,
    )


@pytest.mark.parametrize("missing", LAYOUT_FIELDS)
def test_layout_reader_requires_all_keys_even_when_not_ready(tmp_path, missing):
    path = tmp_path / "layout.json"
    value = {**frame(), "ready": False}
    del value[missing]
    write_json(path, value)
    with pytest.raises(EvidenceError, match=missing):
        read_layout(path)


@pytest.mark.parametrize("writer", [write_json, write_text])
def test_atomic_writer_preserves_published_file_during_partial_staging(tmp_path, monkeypatch, writer):
    from pathlib import Path

    path = tmp_path / "evidence.json"
    old = '{"previous": true}\n'
    path.write_text(old)
    write = Path.write_text
    observed = []

    def staged(temporary, payload, *args, **kwargs):
        assert temporary.parent == path.parent and temporary != path
        write(temporary, payload[: len(payload) // 2], *args, **kwargs)
        # The staged file is incomplete. Readers still see the old complete file.
        observed.append(json.loads(path.read_text()))
        assert path.read_text() == old
        return write(temporary, payload, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", staged)
    payload = {"next": "publication"} if writer is write_json else '{"next":"publication"}'
    writer(path, payload)
    assert observed == [{"previous": True}]
    assert json.loads(path.read_text()) == {"next": "publication"}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("operation", ["write_text", "replace"])
def test_atomic_write_failure_preserves_original_and_removes_staging(tmp_path, monkeypatch, operation):
    from pathlib import Path

    path = tmp_path / "report.json"
    write_json(path, {"previous": True})
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("evidence disk failure")

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(OSError, match="evidence disk failure"):
        write_json(path, {"next": True})
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_writers_use_unique_staging_files(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Barrier

    path = tmp_path / "report.json"
    barrier = Barrier(4)
    replace = Path.replace
    staged = []

    def publish(temporary, target):
        staged.append(temporary)
        barrier.wait(timeout=10)
        return replace(temporary, target)

    monkeypatch.setattr(Path, "replace", publish)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda generation: write_json(path, {"generation": generation}), range(4)))
    assert len(set(staged)) == 4
    assert json.loads(path.read_text())["generation"] in range(4)
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("raw", ['{"ready":true}', '{"action_generation":'])
def test_ui_mutate_rejects_bad_publication_after_click(tmp_path, monkeypatch, raw):
    from e2e_ui import mutate

    path = tmp_path / "layout.json"
    write_layout(path, frame())

    class Session:
        root, timeout = tmp_path, 1

        def widget_click(self, widget):
            path.write_text(raw)

    monkeypatch.setattr("e2e_ui.time.sleep", lambda _: pytest.fail("Invalid evidence must not be retried"))
    with pytest.raises(EvidenceError, match=r"layout\.json"):
        mutate(Session(), "fixture-action", "applied")


def test_rejected_layout_is_never_published(tmp_path):
    path = tmp_path / "layout.json"
    write_layout(path, frame())
    before = path.read_bytes()
    with pytest.raises(EvidenceError, match="missing required layout keys"):
        write_layout(path, {"ready": False})
    assert path.read_bytes() == before


def test_demo_vm_never_selects_a_real_backend(monkeypatch):
    from e2e_ui import main

    monkeypatch.setattr("e2e_ui.LocalBackend", lambda *args: pytest.fail("Must not reach host backend"))
    with pytest.raises(SystemExit) as error:
        main(["--demo-vm"])
    assert error.value.code == 2


def test_evidence_error_still_interrupts_child_and_closes_session(tmp_path, monkeypatch):
    import e2e_ui

    closed, interrupted = [], []

    class Session:
        timeout = 1

        def __init__(self, evidence, *_args, **_kwargs):
            self.root = evidence
            self.captures = []

        def shot(self, name):
            assert name == "failure"

        def tmux(self, *command):
            assert command == ("send-keys", "C-c")
            interrupted.append(True)
            write_json(self.root / "result.json", {"status": "FAIL", "guard_tripped": False})

        def close(self):
            closed.append(True)

    def fail(_session):
        raise EvidenceError("published layout schema is invalid")

    monkeypatch.setattr(e2e_ui, "UISession", Session)
    monkeypatch.setattr(e2e_ui, "drive_cli", fail)
    with pytest.raises(EvidenceError, match="published layout schema is invalid"):
        e2e_ui.main(["--demo", "--interface", "cli", "--output", str(tmp_path / "run")])
    assert closed == interrupted == [True]

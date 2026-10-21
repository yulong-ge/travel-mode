"""Concurrent workers attribute only their own leaks; the controller sees all."""

from temp_dir_guard import TempGuard, entries


def test_temp_guard_attributes_leak_without_blaming_other_worker(tmp_path):
    system = tmp_path / "system"
    system.mkdir()
    existing = system / "travel-mode-preexisting"
    existing.mkdir()
    before = entries([system])
    first = TempGuard([system], before, tmp_path / "gw0.jsonl", "gw0")
    second = TempGuard([system], before, tmp_path / "gw1.jsonl", "gw1")
    first.test = "test_first.py::test_leaking"
    second.test = "test_second.py::test_live_temporary"
    leaked = system / "travel-mode-leak"
    live = system / "travel-mode-live"
    first.audit("os.mkdir", (str(leaked),))
    leaked.mkdir()
    second.audit("tempfile.mkdtemp", (str(live),))
    live.mkdir()
    own = first.leaks(own=True)
    assert len(own) == 1 and "test_first.py::test_leaking (gw0)" in own[0]
    assert "travel-mode-live" not in own[0]
    live.rmdir()
    assert second.leaks(own=True) == []
    assert first.leaks() == own
    leaked.rmdir()
    assert first.leaks() == [] and existing.exists()

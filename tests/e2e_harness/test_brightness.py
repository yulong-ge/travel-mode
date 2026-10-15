"""Exercise the explicit native boundary using an API-shaped double only."""

import pytest
from e2e_fake import SimulatedBrightness

from e2e_harness.conftest import e2e


def test_native_brightness_product_roundtrip_and_restricted_values(sandbox):
    harness, guard, _ = sandbox
    native = SimulatedBrightness()
    original = native.get()
    harness.brightness_loader = lambda: native
    harness.run()
    assert native.calls == [0.15, original]
    assert native.get() == original
    assert harness.brightness_report["status"] == "PASS"
    assert harness.brightness_report["observed_target"] == 0.15
    assert harness.brightness_report["observed_restored"] == original
    assert not any(c[0] == "brightness" for c in guard.commands)
    with pytest.raises(PermissionError, match="unexpected value"):
        harness.brightness_boundary.set(0.9)
    assert native.calls == [0.15, original]


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_brightness_emergency_restores_before_other_cleanup(sandbox, error_type):
    harness, _, _ = sandbox
    native = SimulatedBrightness()
    original = native.get()
    harness.brightness_loader = lambda: native

    def fail(stage):
        if stage == "brightness-applied":
            raise error_type("brightness injected failure")

    harness.checkpoint = fail
    command = harness.command

    def verify_cleanup(cmd):
        if native.calls:
            assert native.get() == original  # Restored before any slow cleanup command.
        return command(cmd)

    harness.command = verify_cleanup
    with pytest.raises(error_type, match="brightness injected failure"):
        harness.run()
    assert native.calls == [0.15, original]
    assert harness.brightness_report["emergency_restored"] == original


def test_brightness_get_failure_skips_without_set(sandbox, capsys):
    harness, _, _ = sandbox
    native = SimulatedBrightness()

    def unavailable():
        raise RuntimeError("DisplayServicesGetBrightness rc=1000")

    native.get = unavailable
    harness.brightness_loader = lambda: native
    harness.run()
    assert native.calls == []
    assert harness.brightness_report["status"] == "SKIP"
    assert "rc=1000" in capsys.readouterr().out


def test_brightness_failed_off_uses_emergency_backend(sandbox, monkeypatch):
    harness, _, _ = sandbox
    native = SimulatedBrightness()
    original = native.get()
    harness.brightness_loader = lambda: native
    restore = e2e.run_restore

    def failed_off(st, emit):
        return (0, 1) if st.get("brightness") else restore(st, emit)

    monkeypatch.setattr(e2e, "run_restore", failed_off)
    with pytest.raises(AssertionError, match="no failed items"):
        harness.run()
    assert native.calls == [0.15, original] and native.get() == original


def test_brightness_failed_emergency_is_explicit_and_other_cleanup_runs(sandbox):
    harness, _, machine = sandbox
    native = SimulatedBrightness()
    harness.brightness_loader = lambda: native
    setter = native.set

    def fail_restore(value):
        if value != 0.15:
            raise RuntimeError("native restore denied")
        return setter(value)

    native.set = fail_restore

    def fail(stage):
        if stage == "brightness-applied":
            raise RuntimeError("trigger emergency")

    harness.checkpoint = fail
    with pytest.raises(e2e.CleanupError, match="native restore denied"):
        harness.run()
    assert not machine.loaded and list(machine.rows) == [77]
    assert not harness.root.exists()


@pytest.mark.parametrize("phase", ["target", "off", "emergency"])
def test_brightness_assertion_prints_original_target_readback(sandbox, phase, capsys):
    harness, _, _ = sandbox
    native = SimulatedBrightness()
    original = native.get()
    setter = native.set

    def wrong_readback(value):
        result = setter(value)
        if (phase == "target" and value == 0.15) or (phase != "target" and len(native.calls) == 2):
            native.value = 0.3
        return result

    native.set = wrong_readback
    harness.brightness_loader = lambda: native

    def trigger(stage):
        if phase == "emergency" and stage == "brightness-applied":
            raise RuntimeError("trigger brightness emergency")

    harness.checkpoint = trigger
    with pytest.raises(AssertionError, match=r"Brightness|Emergency native brightness"):
        harness.run()
    output = capsys.readouterr().out
    assert f"Brightness: original={original!r}, target=0.15, readback=0.3" in output
    assert native.get() == original  # The independent emergency/cleanup attempt succeeds.

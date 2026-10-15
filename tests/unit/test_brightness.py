"""Brightness backend capability detection and native writes."""

from __future__ import annotations

import pytest

from travel_mode.system import brightness
from travel_mode.system.brightness import CliBrightness, NativeBrightness, select_backend
from travel_mode.system.shell import CmdResult, subprocess_runner


def test_native_brightness_get_and_set_use_display_services():
    calls = []

    class Services:
        def DisplayServicesGetBrightness(self, display, pointer):
            pointer._obj.value = 0.42
            return 0

        def DisplayServicesSetBrightness(self, display, value):
            calls.append((display, value.value))
            return 0

    class Graphics:
        def CGMainDisplayID(self):
            return 1

    backend = NativeBrightness(Services(), Graphics())
    assert backend.get() == pytest.approx(0.42)
    assert backend.set(0.15) == ("", 0)
    assert calls == [(1, pytest.approx(0.15))]


def test_native_brightness_rejects_invalid_get_value():
    class Services:
        def DisplayServicesGetBrightness(self, display, pointer):
            pointer._obj.value = 1000
            return 0

    class Graphics:
        def CGMainDisplayID(self):
            return 1

    with pytest.raises(RuntimeError, match="DisplayServicesGetBrightness"):
        NativeBrightness(Services(), Graphics()).get()


def test_brightness_falls_back_to_cli_only_when_native_load_fails(monkeypatch):
    def missing_framework(path):
        raise OSError(path)

    monkeypatch.setattr("travel_mode.system.brightness.ctypes.CDLL", missing_framework)
    monkeypatch.setattr(
        "travel_mode.system.brightness.shutil.which", lambda name: "/usr/local/bin/brightness"
    )
    assert isinstance(select_backend(), CliBrightness)


def test_brightness_falls_back_to_cli_when_native_get_fails(monkeypatch):
    class Native:
        def get(self):
            raise RuntimeError("native get failed")

    monkeypatch.setattr(NativeBrightness, "load", classmethod(lambda cls: Native()))
    monkeypatch.setattr(
        "travel_mode.system.brightness.shutil.which", lambda name: "/usr/local/bin/brightness"
    )
    assert isinstance(select_backend(), CliBrightness)


def test_brightness_falls_back_to_cli_when_native_get_returns_invalid_value(monkeypatch):
    class Native:
        def get(self):
            return 1000

    monkeypatch.setattr(NativeBrightness, "load", classmethod(lambda cls: Native()))
    monkeypatch.setattr(
        "travel_mode.system.brightness.shutil.which", lambda name: "/usr/local/bin/brightness"
    )
    assert isinstance(select_backend(), CliBrightness)


def test_native_get_failure_rc_1000_is_unavailable_without_cli(monkeypatch):
    class Services:
        def DisplayServicesGetBrightness(self, display, pointer):
            return 1000

    class Graphics:
        def CGMainDisplayID(self):
            return 1

    native = NativeBrightness(Services(), Graphics())
    monkeypatch.setattr(NativeBrightness, "load", classmethod(lambda cls: native))
    monkeypatch.setattr(brightness.shutil, "which", lambda name: None)
    assert select_backend() is None


@pytest.mark.parametrize("operation", ["get", "set"])
def test_selected_native_failures_do_not_fall_back(monkeypatch, operation):
    class Services:
        calls = 0

        def DisplayServicesGetBrightness(self, display, pointer):
            self.calls += 1
            pointer._obj.value = 0.42
            return 0 if self.calls == 1 else 1000

        def DisplayServicesSetBrightness(self, display, value):
            return 1000

    class Graphics:
        def CGMainDisplayID(self):
            return 1

    native = NativeBrightness(Services(), Graphics())
    monkeypatch.setattr(NativeBrightness, "load", classmethod(lambda cls: native))
    monkeypatch.setattr(brightness, "get_runner", lambda: subprocess_runner)
    monkeypatch.setattr(brightness.shutil, "which", lambda name: pytest.fail("unexpected fallback"))
    with pytest.raises(RuntimeError, match="1000"):
        if operation == "get":
            brightness.get_brightness()
        else:
            brightness.set_brightness(0.15)


def test_set_brightness_uses_selected_native_backend(monkeypatch):
    calls = []

    class Native:
        def get(self):
            return 0.42

        def set(self, value):
            calls.append(value)
            return "", 0

    monkeypatch.setattr(NativeBrightness, "load", classmethod(lambda cls: Native()))
    monkeypatch.setattr(brightness, "get_runner", lambda: subprocess_runner)
    assert brightness.set_brightness(0.15) == ("", 0)
    assert calls == [0.15]


def test_cli_get_probe_failure_makes_backend_unavailable(monkeypatch, fake_runner):
    def missing_native(cls):
        raise OSError("missing framework")

    monkeypatch.setattr(NativeBrightness, "load", classmethod(missing_native))
    monkeypatch.setattr(brightness.shutil, "which", lambda name: "/usr/local/bin/brightness")
    fake_runner.responses[("brightness", "-l")] = CmdResult("", "get failed", 1)
    assert select_backend() is None

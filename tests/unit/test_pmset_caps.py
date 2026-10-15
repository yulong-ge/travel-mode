import pytest

from travel_mode.system import platform
from travel_mode.system.platform import map_pmset_key, supported_pmset_keys


def test_unsupported_keys_are_filtered():
    supported = supported_pmset_keys(
        "Battery Power:\n sleep 1\n lowpowermode 0\n",
        "Capabilities for AC Power:\n sleep\n lowpowermode\n",
    )
    assert map_pmset_key("sleep", supported) == "sleep"
    assert map_pmset_key("hibernatemode", supported) is None


def test_low_power_mode_maps_to_powermode_when_needed():
    supported = supported_pmset_keys(
        "Battery Power:\n powermode 0\n",
        "Capabilities for AC Power:\n powermode\n",
    )
    assert map_pmset_key("lowpowermode", supported) == "powermode"


def test_non_macos_is_rejected(monkeypatch, capsys):
    monkeypatch.setattr(platform.sys, "platform", "linux")
    with pytest.raises(SystemExit) as exc:
        platform.ensure_macos()
    assert exc.value.code == 2
    assert "仅支持 macOS" in capsys.readouterr().err

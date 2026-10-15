from travel_mode.config import Config, NetworkConfig
from travel_mode.network_apps import network_scan
from travel_mode.system.shell import CmdResult


def test_network_scan_reports_running_background_and_missing_apps(monkeypatch, fake_runner):
    monkeypatch.setattr(
        "travel_mode.system.apps.app_path",
        lambda name: {
            "Clash Party": "/Applications/Clash Party.app",
            "aTrust": "/Applications/aTrust.app",
            "UURemote": "/Applications/UURemote.app",
        }.get(name, ""),
    )
    fake_runner.responses[("ps", "-axo", "pid=,%cpu=,command=")] = CmdResult(
        "1 1.5 /Applications/Clash Party.app/Contents/MacOS/Clash Party\n"
        "2 0.7 /Applications/aTrust.app/Contents/MacOS/aTrust\n"
        "3 0.3 /Applications/UURemote.app/Contents/Resources/helper\n",
        "",
        0,
    )
    rows = network_scan(Config(network=NetworkConfig(("Clash Party", "aTrust", "UURemote"))))
    by_name = {row["name"]: row for row in rows}
    assert by_name["Clash Party"]["running"]
    assert by_name["aTrust"]["running"]
    assert not by_name["aTrust"]["resident"]
    assert by_name["UURemote"]["status"] == "background"
    assert by_name["UURemote"]["resident"]

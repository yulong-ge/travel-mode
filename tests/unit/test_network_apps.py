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


def test_sourced_standalone_network_helper_is_observed_without_mutation_target(fake_runner):
    fake_runner.responses[("ps", "-axo", "pid=,%cpu=,command=")] = CmdResult(
        "910001 0.7 /Library/Helpers/remoting_me2me_host --start\n"
        "910002 0.3 /Library/Helpers/remoting_me2me_host --worker\n"
        "910003 8.0 /Library/Helpers/unrecognized-vpn --start\n",
        "",
        0,
    )
    rows = network_scan(Config.defaults())
    assert rows == [
        dict(
            name="Chrome Remote Desktop",
            path="",
            running=True,
            status="background",
            resident=True,
            cpu=1.0,
            rule_id="chrome-remote-desktop",
        )
    ]


def test_cli_disconnect_uses_observed_network_rules_and_explicit_configuration():
    from travel_mode.network_apps import disconnect_names

    config = Config(network=NetworkConfig(("Configured VPN",)))
    observed = [
        dict(name="International VPN", path="/Applications/International VPN.app", category="network"),
        dict(name="Configured VPN", path="/Applications/Configured VPN.app", category="network"),
        dict(name="Editor", path="/Applications/Editor.app"),
        dict(name="Ignored VPN", path="/Applications/Ignored.app", category="network", category_ignored=True),
        dict(name="Front VPN", path="/Applications/Front.app", category="network", reason="foreground"),
    ]
    assert disconnect_names(config, observed) == ("Configured VPN", "International VPN")
    assert disconnect_names(Config.defaults(), []) == ()

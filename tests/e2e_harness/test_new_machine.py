"""Clean-machine acceptance rejects private output and invented discovery evidence."""

import base64

import check_new_machine as clean
import pytest


def test_output_guard_allows_public_products_and_rejects_private_identifiers():
    encoded = base64.b64encode(b"private-fixture-label").decode()
    clean.validate_output("Cisco Secure Client, restic, Tailscale, OrbStack", [encoded])
    with pytest.raises(AssertionError, match="private identifier"):
        clean.validate_output("PRIVATE-FIXTURE-LABEL", [encoded])


def test_provenance_guard_requires_observed_paths_and_matching_rule(tmp_path):
    path = tmp_path / "Cisco Secure Client.app"
    path.mkdir()
    row = dict(name=path.stem, path=str(path), bundle_id="", rule_id="cisco-secure-client")
    data = dict(apps=[row], services=[])
    clean.validate_snapshot(data)
    row["rule_id"] = "invented"
    with pytest.raises(AssertionError, match="Unverified"):
        clean.validate_snapshot(data)
    row["rule_id"] = "cisco-secure-client"
    path.rmdir()
    with pytest.raises(AssertionError, match="not observed"):
        clean.validate_snapshot(data)
    with pytest.raises(AssertionError, match="not observed"):
        clean.validate_discovered_paths(dict(apps=[row], services=[]))
    data = dict(
        apps=[],
        services=[
            dict(
                label="org.example.unknown",
                executable="",
                plist_path=str(tmp_path / "agent.plist"),
                cpu="0.1",
            )
        ],
    )
    with pytest.raises(AssertionError, match="not observed"):
        clean.validate_snapshot(data)
    (tmp_path / "agent.plist").touch()
    with pytest.raises(AssertionError, match="neither a rule nor high CPU"):
        clean.validate_snapshot(data)
    data["services"][0]["cpu"] = "95.0"
    clean.validate_snapshot(data)


def test_acceptance_runs_cli_commands_with_fake_machine_and_no_writes(tmp_path, monkeypatch, fake_runner):
    from travel_mode.system import shell

    monkeypatch.setattr(shell, "ReadOnlyRunner", lambda: fake_runner)
    # The fixture root itself may contain the host's opaque temporary-directory id.
    # This acceptance test uses a fixture privacy identity; the real job loads the full list.
    monkeypatch.setattr(
        clean.runpy,
        "run_path",
        lambda _: {
            "PRIVATE_PATTERNS": [base64.b64encode(b"private-fixture-label").decode()],
        },
    )
    output = tmp_path / "acceptance"
    assert clean.main(["--output", str(output)]) == 0
    assert {path.name for path in output.iterdir()} >= {
        "check.stdout",
        "check.stderr",
        "setup.stdout",
        "setup.stderr",
        "suggest.stdout",
        "suggest.stderr",
        "observed.json",
    }
    assert shell.get_runner() is fake_runner
    assert not (output / "config.toml").exists()
    assert not (output / "state.json").exists()
    assert not any(call[0] in {"sudo", "kill", "open", "osascript"} for call in fake_runner.calls)

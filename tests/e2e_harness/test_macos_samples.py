"""Raw parser evidence survives failed reads without allowing mutations."""

import json

import collect_macos_samples as samples
import pytest

from travel_mode.system.shell import CmdResult


@pytest.mark.parametrize("failure", [False, True])
def test_raw_collection_keeps_all_results_and_reports_failed_reads(tmp_path, failure):
    calls = []

    def runner(command, timeout, interactive):
        calls.append((command, timeout))
        assert not interactive
        if command == ["lsappinfo", "front"]:
            return CmdResult("ASN:0x0-0x1:\n", "", 0)
        if command == ["pmset", "-g", "log"] and failure:
            return CmdResult("partial", "timeout", 124)
        return CmdResult('"pid"=358\n"CFBundleIdentifier"="com.apple.finder"\n', "", 0)

    output = tmp_path / "raw"
    errors = samples.collect(output, runner=runner)
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(manifest["results"]) == len(samples.COMMANDS) + 1
    assert manifest["results"]["foreground-info"]["command"][-1] == "ASN:0x0-0x1:"
    assert (output / "foreground-info.stdout").read_text().startswith('"pid"=358')
    assert bool(errors) == failure
    if failure:
        assert errors == ["pmset-log: rc=124; timeout"]
        assert (output / "pmset-log.stderr").read_text() == "timeout"
    assert all(timeout <= 30 for _, timeout in calls)
    assert all("command=" not in " ".join(command) for command, _ in calls)


def test_collector_rejects_mutations_before_runner(tmp_path, monkeypatch):
    monkeypatch.setitem(samples.COMMANDS, "bad", ["pmset", "sleep", "0"])
    # The actual allowlist must not accept a command merely because a caller changed the inventory.
    with pytest.raises(ValueError, match="Not an allowed sample command"):
        samples.collect(tmp_path / "raw", runner=lambda *a, **kw: CmdResult("", "", 0))

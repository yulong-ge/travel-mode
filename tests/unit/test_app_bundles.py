"""Discovery must reject misleading directories without losing the rest of a scan."""

import json
import plistlib

import pytest

from travel_mode.cli import main
from travel_mode.flows.suggest import bundle_for
from travel_mode.system import apps, daemon_discovery, health
from travel_mode.system.app_bundle import InvalidBundle, read_bundle
from travel_mode.system.app_snapshot import AppIndex
from travel_mode.system.shell import CmdResult


def bundle(path, metadata=None, *, executable=False):
    (path / "Contents").mkdir(parents=True)
    info = {"CFBundlePackageType": "APPL", "CFBundleIdentifier": "org.example.worker"}
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps(info if metadata is None else metadata))
    if executable:
        program = path / "Contents/MacOS/worker"
        program.parent.mkdir()
        program.write_text("fixture only; never execute")
        program.chmod(0o755)
    return path


@pytest.mark.parametrize(
    "kind", ["missing", "broken", "dictionary", "plugin", "no-executable", "not-executable"]
)
def test_invalid_bundle_is_excluded_and_logged_once(tmp_path, fake_runner, kind):
    path = tmp_path / "Applications/Az.App"
    path.mkdir(parents=True)
    if kind != "missing":
        (path / "Contents").mkdir()
        info = path / "Contents/Info.plist"
        values = {"dictionary": [], "plugin": {"CFBundlePackageType": "BNDL"}, "no-executable": {}}
        info.write_bytes(
            b"not a plist"
            if kind == "broken"
            else plistlib.dumps(values.get(kind, {"CFBundleExecutable": "worker"}))
        )
        if kind == "not-executable":
            program = path / "Contents/MacOS/worker"
            program.parent.mkdir()
            program.write_text("not executable")
    with pytest.raises(InvalidBundle) as error:
        read_bundle(path)
    assert str(path) in str(error.value)
    valid = bundle(tmp_path / "Applications/Valid.app")
    # The same invalid entry arrives through the directory and Spotlight indexes.
    fake_runner.responses[("mdfind",)] = CmdResult(f"{path}\n{path}\n{valid}\n", "", 0)
    index = AppIndex([str(path.parent)])
    assert index.paths == (str(valid),)
    assert index.find("Az") == ""
    records = [json.loads(line) for line in (tmp_path / "travel-mode.log").read_text().splitlines()]
    assert len(records) == 1 and records[0]["kind"] == "observation"
    assert records[0]["target"] == str(path)


@pytest.mark.parametrize("declared", [True, False])
def test_real_bundle_accepts_appl_or_declared_executable(tmp_path, declared):
    info = {"CFBundlePackageType": "APPL"} if declared else {"CFBundleExecutable": "worker"}
    path = bundle(tmp_path / "Valid.App", info, executable=not declared)
    assert read_bundle(path) == info


def test_non_application_type_cannot_become_app_by_containing_an_executable(tmp_path):
    path = bundle(
        tmp_path / "Plugin.app",
        {"CFBundlePackageType": "BNDL", "CFBundleExecutable": "worker"},
        executable=True,
    )
    with pytest.raises(InvalidBundle, match="BNDL"):
        read_bundle(path)


def test_extra_directory_nested_and_process_discovery_share_bundle_validation(
    tmp_path, monkeypatch, fake_runner
):
    root = tmp_path / "additional"
    invalid = root / "tools/Az.App"
    invalid.mkdir(parents=True)
    valid = bundle(root / "tools/Valid.App")
    monkeypatch.setenv("TRAVEL_MODE_APP_DIRS", str(root))
    assert apps.app_path("Valid") == str(valid)
    assert apps.app_path("Az") == ""
    fake_runner.responses[("ps", "-p", "810", "-o", "comm=")] = CmdResult(
        f"{invalid}/Contents/MacOS/worker", "", 0
    )
    fake_runner.responses[("ps", "-p", "811", "-o", "comm=")] = CmdResult(
        f"{valid}/Contents/MacOS/worker", "", 0
    )
    assert bundle_for("810") is None
    assert bundle_for("811") == str(valid)
    assert health.app_path(f"{invalid}/Contents/MacOS/worker") is None
    assert health.app_path(f"{valid}/Contents/MacOS/worker") == str(valid)
    candidates = daemon_discovery._installed_apps((root,), [])
    assert [row["path"] for row in candidates] == [str(valid)]


@pytest.mark.parametrize(
    "command", [["setup", "--yes", "--dry-run"], ["check", "--json"], ["suggest", "--json"]]
)
def test_bad_spotlight_entry_keeps_whole_cli_result(tmp_path, fake_runner, darwin_platform, capsys, command):
    valid = bundle(tmp_path / "Applications/Valid.app")
    invalid = tmp_path / "usr/local/share/powershell/Modules/Az.App"
    invalid.mkdir(parents=True)
    broken = bundle(tmp_path / "Applications/Broken.app")
    (broken / "Contents/Info.plist").write_text("not a plist")
    fake_runner.responses[("mdfind",)] = CmdResult(f"{invalid}\n{broken}\n{valid}\n", "", 0)
    fake_runner.responses[("ps", "-axo", "pid=,%cpu=,command=")] = CmdResult(
        f"811 2.5 {valid}/Contents/MacOS/worker", "", 0
    )
    fake_runner.responses[("top",)] = CmdResult("PID POWER COMMAND\n810 2.0 worker\n811 3.0 worker\n", "", 0)
    fake_runner.responses[("ps", "-p", "810", "-o", "comm=")] = CmdResult(
        f"{invalid}/Contents/MacOS/worker", "", 0
    )
    fake_runner.responses[("ps", "-p", "811", "-o", "comm=")] = CmdResult(
        f"{valid}/Contents/MacOS/worker", "", 0
    )
    assert main([*command, "--verbose", "--lang", "en"]) == 0
    captured = capsys.readouterr()
    assert "Az.App" in captured.err and "Broken.app" in captured.err
    if "--json" in command:
        data = json.loads(captured.out)
        assert [row["name"] for row in data["apps"]] == ["Valid"]
        assert "audit_error" not in data
    else:
        assert "Valid" in captured.out and "Az.App" not in captured.out
    log_path = next(tmp_path.rglob("travel-mode.log"))
    log = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert sum(row["target"] == str(invalid) and row["kind"] == "observation" for row in log) == 1
    assert not (tmp_path / "config.toml").exists()


@pytest.mark.parametrize(
    "command", [["setup", "--yes", "--dry-run"], ["suggest", "--json"], ["check", "--json"]]
)
def test_scan_command_errors_have_stderr_and_nonzero_status(fake_runner, darwin_platform, capsys, command):
    fake_runner.responses[("mdfind",)] = CmdResult("", "fixture index timed out", 124)
    assert main([*command, "--lang", "en"]) != 0
    captured = capsys.readouterr()
    assert "fixture index timed out" in captured.err
    if command[0] == "check":
        assert "audit_error" in json.loads(captured.out)
    else:
        assert "fixture index timed out" not in captured.out

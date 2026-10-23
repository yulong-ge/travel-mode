"""Only temporary plugin folders are writable; no SwiftBar refresh is emitted."""

import json
import os
from pathlib import Path

import pytest

from travel_mode import cli, menubar
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.actions.menubar import InstallMenubar, UninstallMenubar
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


@pytest.fixture
def plugin_folder(tmp_path, fake_runner):
    folder = tmp_path / "Plugin Folder"
    folder.mkdir()
    fake_runner.responses[tuple(menubar.PREFERENCE)] = CmdResult(str(folder) + "\n", "", 0)
    return folder


def test_copy_install_status_uninstall_only_our_file(plugin_folder, fake_runner, darwin_platform, capsys):
    other = plugin_folder / "other.5m.sh"
    other.write_text("untouched")
    assert cli.main(["menubar", "install"]) == 0
    path = plugin_folder / menubar.FILENAME
    assert not path.is_symlink() and path.read_bytes() == menubar.payload()
    assert path.stat().st_mode & 0o777 == 0o755
    capsys.readouterr()
    assert cli.main(["menubar", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == dict(
        path=str(path),
        installed=True,
        current=True,
        executable=True,
        installed_version="2.2.0",
        bundled_version="2.2.0",
        show="always",
        hidden=False,
        running=False,
        notice=menubar.t("menubar.not_running"),
    )
    assert cli.main(["menubar", "uninstall"]) == 0
    assert not path.exists() and other.read_text() == "untouched"
    query = ("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")
    assert fake_runner.calls == [
        tuple(menubar.PREFERENCE),
        query,
        tuple(menubar.PREFERENCE),
        query,
        tuple(menubar.PREFERENCE),
    ]


def test_install_requires_force_for_different_content(plugin_folder):
    path = plugin_folder / menubar.FILENAME
    path.write_text("old")
    with pytest.raises(menubar.MenubarError, match="--force"):
        Executor(Context(None)).execute(InstallMenubar())
    Executor(Context(None)).execute(InstallMenubar(force=True))
    assert path.read_bytes() == menubar.payload()


@pytest.mark.parametrize("kind", ["symlink", "directory", "foreign"])
def test_uninstall_refuses_unowned_target(plugin_folder, kind):
    path = plugin_folder / menubar.FILENAME
    if kind == "symlink":
        path.symlink_to(plugin_folder / "missing")
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_text("unrelated")
    with pytest.raises(menubar.MenubarError):
        Executor(Context(None)).execute(UninstallMenubar())


@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_dry_run_preserves_preference_target_and_files(
    plugin_folder, operation, darwin_platform, capsys, fake_runner
):
    path = plugin_folder / menubar.FILENAME
    path.write_bytes(menubar.payload())
    # Reads may update atime (the CI failure changed only this field).
    os.utime(path, ns=(1, path.stat().st_mtime_ns))
    before = path.stat()
    assert cli.main(["menubar", operation, "--dry-run"]) == 0
    assert str(path) in capsys.readouterr().out
    assert path.read_bytes() == menubar.payload()
    after = path.stat()
    for key in (
        "st_mode",
        "st_ino",
        "st_dev",
        "st_nlink",
        "st_uid",
        "st_gid",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    ):
        assert getattr(after, key) == getattr(before, key), key
    assert fake_runner.calls == [tuple(menubar.PREFERENCE)]


@pytest.mark.parametrize("output,rc", [("", 1), ("relative", 0), ("/not/a/folder", 0)])
def test_directory_error_is_explicit(output, rc, fake_runner, darwin_platform, capsys):
    fake_runner.responses[tuple(menubar.PREFERENCE)] = CmdResult(output, "missing preference", rc)
    assert cli.main(["menubar", "status"]) == 1
    assert "SwiftBar" in capsys.readouterr().err


def test_packaged_copy_matches_integration_and_defaults_is_readonly():
    assert menubar.payload() == Path("integrations/swiftbar/travel-mode.5m.sh").read_bytes()
    assert shell.SYSTEM_TOOLS["defaults"] == "/usr/bin/defaults"
    assert shell._dry_run_readonly(menubar.PREFERENCE)
    assert not shell._dry_run_readonly(["defaults", "write", "com.ameba.SwiftBar", "PluginDirectory", "x"])
    assert not shell._dry_run_readonly(["defaults", "read", "another.domain"])


@pytest.mark.parametrize("language,yes,no", [("en", "yes", "no"), ("zh", "是", "否")])
def test_plain_status_localizes_booleans_but_json_retains_types(
    plugin_folder, language, yes, no, monkeypatch, darwin_platform, capsys
):
    monkeypatch.setenv("TRAVEL_MODE_LANG", language)
    for installed in (False, True):
        if installed:
            Executor(Context(None)).execute(InstallMenubar())
            capsys.readouterr()
        assert cli.main(["menubar", "status"]) == 0
        text = capsys.readouterr().out
        assert (yes if installed else no) in text
        assert not any(word in text for word in ("True", "False", "None"))
        assert cli.main(["menubar", "status", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert all(data[key] is installed for key in ("installed", "current", "executable"))


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("running", [False, True])
def test_install_and_status_explain_manual_launch_and_login_setting(
    plugin_folder, swiftbar_process, fake_runner, language, running, monkeypatch, darwin_platform, capsys
):
    monkeypatch.setenv("TRAVEL_MODE_LANG", language)
    if not running:
        fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult("", "", 0)
    for command in (["menubar", "install"], ["menubar", "status"]):
        assert cli.main(command) == 0
        output = capsys.readouterr().out
        notice = menubar.t("menubar.not_running")
        assert (notice in output) is not running
    assert cli.main(["menubar", "status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["running"] is running
    assert data["notice"] == (None if running else notice)
    assert not any(call[0] in {"open", "osascript"} for call in fake_runner.calls)
    assert not any(call[:2] == ("defaults", "write") for call in fake_runner.calls)

"""CLI 返回值回归测试，外部命令仅经过 FakeRunner。"""

import io
import sys

import pytest

from travel_mode.cli import main
from travel_mode.i18n import get_language, set_language
from travel_mode.system import apps as system_apps
from travel_mode.system import platform, shell


@pytest.mark.parametrize(
    "argv",
    [
        ["--dry-run", "--yes"],
        ["--dry-run", "--yes", "--aggressive"],
        ["--dry-run", "on", "--profile", "flight", "--yes"],
    ],
)
def test_dry_run_main_returns_zero(argv, monkeypatch, tmp_paths, fake_runner, capsys):
    # 平台与程序可用性独立于主机；分派、装饰器和 apply 流程均使用真实实现。
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform.shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(system_apps, "APP_DIRS", [str(tmp_paths / "Applications")])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    previous_language = get_language()
    monkeypatch.setenv("TRAVEL_MODE_LANG", "en")
    try:
        assert main(argv) == 0

        assert "commands would be executed" in capsys.readouterr().out
        assert shell.get_runner() is fake_runner
        assert not shell.is_dry_run()
        assert not (tmp_paths / "state.json").exists()
        assert ("pmset", "-g", "custom") in fake_runner.calls
        assert not any(call[:2] == ("sudo", "pmset") for call in fake_runner.calls)
    finally:
        set_language(previous_language)

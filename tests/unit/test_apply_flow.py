import pytest

import travel_mode.actions.runtime as runtime
import travel_mode.flows.apply as flow_apply
from travel_mode.config import Config
from travel_mode.i18n import get_language, set_language
from travel_mode.profiles import profile_scope
from travel_mode.system import apps as system_apps
from travel_mode.system import launchd, pmset, spotlight
from travel_mode.system.shell import CmdResult


def test_mode_apply_yes_records_only_safe_commands(monkeypatch, tmp_paths, fake_runner):
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(
        launchd,
        "svc_scan",
        lambda config=None: {"unloadable": [], "kept": [], "candidates": [], "invalid": []},
    )
    monkeypatch.setattr(spotlight, "spotlight_on", lambda: False)
    monkeypatch.setattr(pmset, "on_ac_power", lambda: False)
    monkeypatch.setattr(flow_apply, "shutil", type("Shutil", (), {"which": staticmethod(lambda _: None)}))

    assert flow_apply.mode_apply(yes=True) == 0
    mutations = [call for call in fake_runner.calls if call[0] == "sudo"]
    assert mutations
    assert all("restoredefaults" not in call for call in mutations)
    assert all("networksetup" not in call for call in mutations)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_mode_apply_formats_caffeinate_process_rows(monkeypatch, tmp_paths, fake_runner, capsys, language):
    fake_runner.responses[("pmset", "-g", "assertions")] = CmdResult(
        "\n".join(
            f'pid {pid}(caffeinate): PreventUserIdleSystemSleep named: "test"' for pid in range(412, 416)
        ),
        "",
        0,
    )
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        "\n".join(f"{pid} 1 S Thu Oct 15 12:00:00 2026 /usr/bin/caffeinate" for pid in range(412, 416)), "", 0
    )
    fake_runner.responses[("ps", "-ww", "-axo", "pid=,command=")] = CmdResult(
        "412 caffeinate -dimsu\n413 caffeinate -t 60\n414 caffeinate\n415 caffeinate -i\n", "", 0
    )
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(
        launchd,
        "svc_scan",
        lambda config=None: {"unloadable": [], "kept": [], "candidates": [], "invalid": []},
    )
    monkeypatch.setattr(spotlight, "spotlight_on", lambda: False)
    monkeypatch.setattr(pmset, "on_ac_power", lambda: False)
    monkeypatch.setattr(
        flow_apply,
        "shutil",
        type("Shutil", (), {"which": staticmethod(lambda _: None)}),
    )

    previous_language = get_language()
    try:
        set_language(language)
        with profile_scope(Config.defaults(), "flight"):
            assert flow_apply.mode_apply(yes=True, dry_run=True) == 0
    finally:
        set_language(previous_language)
        runtime._finish_dry_run()
    output = capsys.readouterr().out
    for pid, args in ((412, "-dimsu"), (413, "-t 60"), (414, ""), (415, "-i")):
        assert f"PID {pid}" in output
        assert f"caffeinate {args}".strip() in output
    assert not any(call[0] in {"kill", "pkill"} for call in fake_runner.calls)
    assert "sleep 1" in output


@pytest.mark.parametrize("language", ["en", "zh"])
def test_scan_apps_formats_orphan_process_rows(monkeypatch, fake_runner, language):
    monkeypatch.setattr(system_apps, "app_path", lambda name: "")
    fake_runner.responses[("ps", "-axo", "pid=,%cpu=,command=")] = CmdResult(
        "12 0.1 Google Chrome helper\n13 0.2 Google Chrome worker\n14 0.3 Google Chrome extra\n", "", 0
    )
    previous_language = get_language()
    try:
        set_language(language)
        messages = system_apps.scan_apps()["nobundle"]
    finally:
        set_language(previous_language)
    assert len(messages) == 1
    assert "12 Google Chrome helper; 13 Google Chrome worker" in messages[0]
    assert "14 Google Chrome extra" not in messages[0]

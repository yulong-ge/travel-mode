"""Readable receipts preserve original history and use the local timezone."""

import copy
import json
import time
from pathlib import Path

import pytest

from travel_mode import sessions, state
from travel_mode.cli import main
from travel_mode.i18n import set_language
from travel_mode.system.shell import CmdResult


def report_fixture():
    return json.loads((Path(__file__).parents[1] / "fixtures/session-wording.json").read_text())


@pytest.fixture
def shanghai(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    yield
    # The enclosing hermetic fixture restores TZ after this test.


EXPECTED = {
    "zh": """会话报告：外出
10 月 17 日 16:26 – 10 月 20 日 09:01（2 天 16 小时 34 分）
电量 100% → 100%。期间接过电源，无法统计耗电和平均功耗。
执行 13 项：成功 11 项，失败 2 项。
  ✗ Lark：拒绝退出，可能正在显示确认框。（已处理）
恢复 14 项，全部完成。
  曾失败、重试后已恢复：Spotlight 索引（第一次命令未完成）。
暂停期间又运行了：Lark。""",
    "en": """Session report: On the go
Oct 17 16:26 – Oct 20 09:01 (2 days 16 hours 34 minutes)
Battery 100% → 100%. Power was connected during the session; discharge and average power cannot be calculated.
Executed 13 actions: 11 succeeded, 2 failed.
  ✗ Lark: Refused to quit; a confirmation dialog may be open. (resolved)
Restored 14 items; all complete.
  Initially failed, restored after retry: Spotlight indexing (first attempt: command did not complete).
Ran again while paused: Lark.""",
}


@pytest.mark.parametrize("language", ["zh", "en"])
def test_report_full_localized_output_and_legacy_restore(shanghai, language):
    set_language(language)
    report = report_fixture()
    original = copy.deepcopy(report)
    assert "\n".join(sessions.describe(report)) == EXPECTED[language]
    assert report == original


@pytest.mark.parametrize("language", ["zh", "en"])
def test_known_password_failure_uses_first_attempt_reason(shanghai, language):
    set_language(language)
    report = report_fixture()
    report["failures"][1].update(rc=77, kind="not_executed", target="restore:spotlight_off")
    text = "\n".join(sessions.describe(report))
    assert (
        "第一次缺少管理员密码" if language == "zh" else "first attempt: administrator password not provided"
    ) in text
    assert "timeout" not in text and "124" not in text


@pytest.mark.parametrize(
    "seconds,zh,en",
    [
        (0, "不足 1 分钟", "less than 1 minute"),
        (59, "不足 1 分钟", "less than 1 minute"),
        (60, "1 分", "1 minute"),
        (3599, "59 分", "59 minutes"),
        (3600, "1 小时", "1 hour"),
        (86399, "23 小时 59 分", "23 hours 59 minutes"),
        (86400, "1 天", "1 day"),
        (232458, "2 天 16 小时 34 分", "2 days 16 hours 34 minutes"),
    ],
)
def test_report_duration(seconds, zh, en):
    from travel_mode.session_presentation import duration

    for language, expected in (("zh", zh), ("en", en)):
        set_language(language)
        assert duration(seconds) == expected


@pytest.mark.parametrize("language", ["zh", "en"])
def test_report_cross_year_and_local_date(shanghai, language):
    set_language(language)
    report = report_fixture()
    report.update(
        started_at="2025-12-31T15:00:00+00:00", ended_at="2025-12-31T17:00:00+00:00", duration_seconds=7200
    )
    text = list(sessions.describe(report))[1]
    assert "2025" in text and "2026" in text and "23:00" in text and "01:00" in text


def test_report_uses_process_local_timezone_not_a_fixed_offset(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    set_language("en")
    assert list(sessions.describe(report_fixture()))[1] == (
        "Oct 17 04:26 – Oct 19 21:01 (2 days 16 hours 34 minutes)"
    )


def test_unresolved_restore_is_not_presented_as_recovered(shanghai):
    report = report_fixture()
    report["restore"]["failed"] = 1
    set_language("zh")
    text = "\n".join(sessions.describe(report))
    assert "仍有 1 项未恢复" in text and "✗ Spotlight 索引：命令未完成" in text
    assert "重试后已恢复" not in text


def test_history_verbose_and_json_preserve_raw_data(shanghai, darwin_platform, capsys):
    report = report_fixture()
    sessions.append_history(report, 50)
    path = sessions.history_path()
    original = path.read_bytes()
    assert main(["history", "--lang", "zh"]) == 0
    assert capsys.readouterr().out.strip() == EXPECTED["zh"]
    assert main(["history", "--verbose", "--lang", "zh"]) == 0
    verbose = capsys.readouterr().out
    for raw in ("4687 mAh", "4853 mAh", "124", "timeout", "sudo mdutil -i on /", report["started_at"]):
        assert raw in verbose
    assert main(["history", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["sessions"][0]
    result.pop("comparison")
    assert result == report and path.read_bytes() == original


@pytest.mark.parametrize(
    "log,start_external,end_external,expected",
    [
        ("", False, False, "battery_only"),
        ("2026-10-17 07:00:00 +0000 Using AC", False, False, "battery_only"),
        ("2026-10-17 07:00:00 +0000 Using Batt", False, False, "battery_only"),
        (
            "2026-10-18 10:00:00 +0000 Using AC\n2026-10-18 11:00:00 +0000 Using Batt",
            False,
            False,
            "external_seen",
        ),
        ("", True, False, "external_seen"),
        ("", False, True, "external_seen"),
        ("", None, False, "unknown"),
        ("2026-10-17 07:00:00 +0000 Using AC", None, False, "external_seen"),
        ("2026-10-21 07:00:00 +0000 Using AC", False, False, "battery_only"),
    ],
)
def test_power_log_combines_start_observation_and_change_events(
    fake_runner, monkeypatch, log, start_external, end_external, expected
):
    fixture = report_fixture()
    fixture["start"]["external"] = start_external
    fixture["end"].update(external=end_external, percent=90, remaining_mah=4200)
    st = state.new_state()
    st.update(
        scenario="work",
        session=dict(
            id="power-fixture",
            started_at=fixture["started_at"],
            start=fixture["start"],
            external_seen=False,
            actions=dict(attempted=0, succeeded=0, failed=0),
            restored=0,
            restore_failures=0,
            resurrected=[],
        ),
    )
    monkeypatch.setattr(sessions, "observe", lambda: fixture["end"])
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult(log, "", 0)
    assert sessions.build_report(st, 0, 0)["power_status"] == expected


def test_power_log_read_failure_remains_unknown(fake_runner, monkeypatch):
    fixture = report_fixture()
    monkeypatch.setattr(sessions, "observe", lambda: fixture["start"])
    st = state.new_state()
    sessions.begin(st)
    monkeypatch.setattr(sessions, "observe", lambda: fixture["end"])
    fake_runner.responses[("pmset", "-g", "log")] = CmdResult("", "read denied", 1)
    report = sessions.build_report(st, 0, 0)
    assert report["power_status"] == "unknown" and report["errors"]

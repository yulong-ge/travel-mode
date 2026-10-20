"""Name real observed sources; never infer an owner from a shell substring."""

from health_source_fixture import populate
from travel_mode import health_view, sleep_readiness
from travel_mode.i18n import set_language
from travel_mode.system import health


def collect():
    return health.collect(clock=lambda: 0)


def test_caffeinate_follows_parent_chain_and_estimates_remaining(fake_runner):
    populate(fake_runner)
    row = next(r for r in collect()["checks"] if r["id"] == "keep_awake")
    subject = row["subjects"][0]
    assert subject["source"]["name"] == "Claude Code"
    assert subject["source"]["pid"] == 900101 and subject["remaining_seconds"] == 120
    assert len(subject["parent_chain"]) == 2


def test_caffeinate_unknown_parent_is_explicit_not_guessed(fake_runner):
    populate(fake_runner, parent=False)
    set_language("zh")
    report = collect()
    view = sleep_readiness.from_report(report)
    text = view["items"][0]["message"]
    assert text == "某个程序通过系统自带的 caffeinate 命令阻止空闲睡眠（剩余约 2 分钟）。合盖仍会睡眠。"
    assert "Claude" not in text


def test_restart_warning_requires_new_child_and_same_live_parent(fake_runner):
    populate(fake_runner)
    first = sleep_readiness.from_report(collect())
    assert not first["items"][0]["caffeinate"]["restarted"]
    sleep_readiness.store(first)
    same = sleep_readiness.from_report(collect())
    assert not same["items"][0]["caffeinate"]["restarted"]
    populate(fake_runner, child=900104)
    restarted = sleep_readiness.from_report(collect())
    assert restarted["items"][0]["caffeinate"]["restarted"]
    assert "停止后它可能再次启动" in restarted["items"][0]["message"]
    populate(fake_runner, child=900104, parent=False)
    assert not sleep_readiness.from_report(collect())["items"][0]["caffeinate"]["restarted"]


def test_high_cpu_names_non_bundle_processes_and_limits_to_three(fake_runner):
    populate(fake_runner, load=True)
    row = next(r for r in health_view.presentation(collect()) if r["id"] == "high_cpu")
    assert all(name in row["summary"] for name in ("worker-0", "worker-1", "worker-2", "85%", "65%", "55%"))
    assert "worker-3" not in row["summary"] and "后台程序" not in row["summary"]


def test_two_caffeinate_instances_from_one_source_share_one_row(fake_runner):
    from travel_mode.caffeinate_view import groups

    populate(fake_runner)
    row = next(r for r in collect()["checks"] if r["id"] == "keep_awake")
    subjects = [row["subjects"][0], dict(row["subjects"][0], pid=900105)]
    grouped = groups(subjects)
    assert len(grouped) == 1 and len(grouped[0]["members"]) == 2
    assert not groups(subjects, grouped[0]["members"])[0]["restarted"]
    reused = [dict(subjects[0], source=dict(subjects[0]["source"], started="Fri Oct 16 12:00:00 2026"))]
    assert not groups(reused, grouped[0]["members"])[0]["restarted"]


def test_shell_mentioning_caffeinate_is_not_detected(fake_runner):
    from travel_mode.system.health import PROCESS_COMMAND
    from travel_mode.system.shell import CmdResult

    populate(fake_runner)
    result = fake_runner.responses[tuple(PROCESS_COMMAND)]
    fake_runner.responses[tuple(PROCESS_COMMAND)] = CmdResult(
        result.stdout.replace("/usr/bin/caffeinate", "/bin/zsh"), "", 0
    )
    row = next(r for r in collect()["checks"] if r["id"] == "keep_awake")
    assert row["status"] == "clear"

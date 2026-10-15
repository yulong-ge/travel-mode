"""One process has one primary display location; JSON raw evidence stays complete."""

from travel_mode.health_view import display_checks


def test_process_reference_dedup_preserves_raw_evidence():
    rows = []
    for key in ["keep_awake", "assertions", "high_cpu"]:
        rows.append(
            dict(
                id=key,
                title=key,
                evidence=["PID 777: caffeinate"],
                actions=[{"kind": "caffeinate"}],
                impact="impact",
                manual="manual",
            )
        )
    report = {"checks": rows}
    view = display_checks(report)
    assert view[0]["evidence"] == ["PID 777: caffeinate"]
    for row in view[1:]:
        assert row["evidence"] == [] and row["references"] == ["keep_awake"] and row["actions"] == []
    assert all(row["evidence"] == ["PID 777: caffeinate"] for row in rows)


def test_mixed_findings_do_not_duplicate_actions_or_reference_themselves():
    first = dict(
        id="keep_awake",
        title="tools",
        evidence=["PID 777: Awake"],
        actions=[dict(kind="quit", path="/Applications/Awake.app")],
        impact="impact",
        manual="manual",
    )
    second = dict(
        id="assertions",
        title="assertions",
        evidence=[
            "pid 777(Awake): PreventSystemSleep",
            "pid 888(Worker): PreventSystemSleep",
            "pid 888(Worker): PreventUserIdleSystemSleep",
        ],
        actions=[
            dict(kind="quit", path="/Applications/Awake.app"),
            dict(kind="quit", path="/Applications/Worker.app"),
        ],
        impact="impact",
        manual="manual",
    )
    view = display_checks(dict(checks=[first, second]))
    assert view[1]["references"] == ["tools"]
    assert len(view[1]["evidence"]) == 2
    assert view[1]["actions"] == [dict(kind="quit", path="/Applications/Worker.app")]
    assert len(second["evidence"]) == 3 and len(second["actions"]) == 2

"""Widget-free content contracts for the existing pages.

No layout, colour or widget references belong in these view models.
"""

from travel_mode.i18n import t


def plan_controls(state, *, group, app_running=False):
    """The sole control policy shared by rows, renderers and execution tests."""
    if state == "planned":
        return ["select", "cycle"] if group == "apps" else ["select"]
    if state == "failed" and group == "apps" and app_running:
        return ["retry", "force"]
    if state not in {"planned", "running", "done", "warning", "failed", "skipped"}:
        raise ValueError(f"Unknown plan state: {state}")
    return []


def app_row(app, *, state="running", failure=None):
    operations = {
        "running": ["quit", "freeze", "skip"],
        "refused": ["retry_quit", "force_quit", "freeze", "skip"],
        "error": ["quit", "freeze", "skip"],
        "exited": [],
        "frozen": [],
        "skipped": [],
        "protected": [],
    }[state]
    explanation = failure or (t("setup.reason_" + app["reason"]) if app.get("reason") else "")
    if state in {"exited", "frozen", "skipped", "protected"}:
        explanation = t("app_view." + state)
    return dict(
        name=app["name"],
        path=app["path"],
        default_action=app["defact"],
        state=state,
        operations=list(operations),
        explanation=explanation,
    )


def refused(failure):
    if not failure:
        return False
    # Version-1 receipts before quit_status retained the original Apple Event code.
    return failure.get("quit_status") == "refused" or (
        "quit_status" not in failure and "(-128)" in failure.get("stderr", "") + failure.get("stdout", "")
    )


def plan_item(key, group, name, action, reason, descriptor, *, selected=True, evidence=()):
    return dict(
        id=key,
        group=group,
        name=name,
        action=action,
        default_action=action,
        reason=reason,
        selected=selected,
        state="planned",
        descriptor=descriptor,
        evidence=list(evidence),
        app_running=descriptor["kind"] == "app",
        result="",
        operations=plan_controls("planned", group="apps" if descriptor["kind"] == "app" else group),
    )


def plan_view(row):
    """Localized row content and permitted controls, independent of widgets."""
    kind = row["descriptor"]["kind"]
    controls = (
        []
        if row.get("fixed")
        else plan_controls(
            row["state"], group="apps" if kind == "app" else row["group"], app_running=row["app_running"]
        )
    )
    state = row["state"]
    if state == "planned":
        action = target_label(row) if row["action"] == "apply" else t("redesign." + row["action"])
    elif state in {"running", "skipped"}:
        action = t("redesign.row_" + state)
    elif state == "failed":
        action = t("redesign.failed_short")
    elif state == "warning":
        action = t(
            "app_view.exited" if row.get("warning_key") == "failure.quit_helpers" else "redesign.forced_short"
        )
    elif row.get("planned_result"):
        action = t("redesign.planned_short")
    elif kind == "app":
        action = t(
            "redesign.row_thawed"
            if row["action"] == "thaw"
            else "app_view.frozen"
            if row["action"] == "freeze"
            else "app_view.exited"
        )
    else:
        action = t("redesign.row_done")
    reason = t(row["reason_key"]) if row.get("reason_key") else row["reason"]
    if kind == "app":
        if row["action"] in {"quit", "force"}:
            reason = t("redesign.quit_reason")
        elif row["action"] == "freeze" and row.get("reason_key") == "redesign.quit_reason":
            reason = t("setup.reason_freeze")
    if state == "warning":
        reason = t(row["warning_key"], name=row["name"]) if row.get("warning_key") else row["result"]
        reason = reason.removeprefix(row["name"]).strip()
    elif state == "failed":
        reason = t(
            "redesign.freeze_failed"
            if row["action"] == "freeze"
            else "redesign.refused_short"
            if row.get("quit_status") == "refused"
            else "redesign.quit_timeout"
            if row.get("quit_status") == "timeout"
            else "redesign.failed_reason"
        )
    return dict(
        name=power_name(row["descriptor"])
        if kind == "power"
        else t(row["name_key"])
        if row.get("name_key")
        else row["name"],
        action=action,
        reason=reason,
        operations=controls,
        mark={"planned": "·", "running": "◌", "done": "✓", "warning": "▲", "failed": "✗", "skipped": "○"}[
            state
        ],
    )


def power_name(descriptor):
    key = descriptor["values"][0]
    key = "lowpowermode" if key == "powermode" else key
    name = t(
        "redesign.hibernation"
        if key == "hibernatemode" and descriptor["values"][1] == "25"
        else "preview.name." + key
    )
    if key in {"powernap", "womp"}:
        name = t(
            "redesign.scoped_name",
            name=name,
            scope=t("redesign.scope_ac" if descriptor["domain"] == "-c" else "redesign.scope_battery"),
        )
    return name


def target_label(row):
    descriptor = row["descriptor"]
    if descriptor["kind"] == "brightness":
        return t("health.action.brightness", percent=round(float(descriptor["value"]) * 100))
    if descriptor["kind"] != "power":
        return t("redesign.apply_action")
    key, value = descriptor["values"]
    if key in {"sleep", "displaysleep", "disksleep"}:
        return t("redesign.target_minutes", minutes=value) if value != "0" else t("redesign.target_never")
    if key == "hibernatemode":
        return t("redesign.target_deeper" if value == "25" else "redesign.target_standard")
    return t("redesign.target_on" if value == "1" else "redesign.target_off")


def evidence_lines(row):
    """Labeled technical facts, separate from the one-line action result."""
    fields = row.get("detail_fields", {})
    lines = []
    for key in ("path", "cpu"):
        if key in fields:
            lines.append(t("redesign.evidence_" + key, value=fields[key]))
    if "command" in fields:
        command = t("redesign.evidence_command", value=fields["command"])
        if fields.get("returncode") is not None:
            command += " · " + t("redesign.evidence_exit", value=fields["returncode"])
        if fields.get("apple_event") is not None:
            command += " · Apple Event " + str(fields["apple_event"])
        lines.append(command)
    for value in row.get("evidence", ()):
        if (
            value
            and value != row.get("result")
            and value not in {str(fields.get("path", "")), str(fields.get("cpu", ""))}
        ):
            lines.append(t("redesign.evidence_observation", value=value))
    return lines


def satisfied_lines(values):
    """One wrapping sentence per supply domain, without repeating its prefix."""
    if not values:
        return []
    if all(isinstance(value, str) for value in values):
        return [t("redesign.satisfied", names=t("redesign.satisfied_separator").join(values))]
    grouped = {}
    for descriptor in values:
        key = descriptor["values"][0]
        key = "lowpowermode" if key == "powermode" else key
        name = t("preview.name." + key)
        if key not in {"lowpowermode", "powernap"}:
            name = name[0].lower() + name[1:]
        label = t(
            "redesign.satisfied_product_value" if name[-1].isascii() else "redesign.satisfied_value",
            name=name,
            target=target_label(dict(descriptor=descriptor)).lower(),
        )
        grouped.setdefault(descriptor["domain"], []).append(label)
    return [
        t(
            "redesign.satisfied_battery" if domain == "-b" else "redesign.satisfied_ac",
            names=t("redesign.satisfied_separator").join(names),
        )
        for domain, names in grouped.items()
    ]

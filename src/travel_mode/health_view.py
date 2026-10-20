"""Deduplicated health presentation; raw observations remain intact in JSON."""

import re
from copy import deepcopy

from .i18n import t


def display_checks(report):
    owners = {}
    action_owners = set()
    result = []
    for original in report["checks"]:
        row = deepcopy(original)
        row["references"] = []
        evidence = []
        for line in row["evidence"]:
            pids = re.findall(r"\b(?:PID|pid)\s+(\d+)\b", line)
            prior = [owners[pid] for pid in pids if pid in owners and owners[pid] != row["title"]]
            if prior:
                row["references"].extend(prior)
            else:
                evidence.append(line)
                for pid in pids:
                    owners[pid] = row["title"]
        row["evidence"] = evidence
        row["references"] = list(dict.fromkeys(row["references"]))
        actions = []
        for action in row["actions"]:
            key = (
                ("app", action["path"])
                if "path" in action
                else ("pid", action["process"]["pid"])
                if action["kind"] == "caffeinate" and "process" in action
                else None
            )
            if key is None or key not in action_owners:
                actions.append(action)
                if key is not None:
                    action_owners.add(key)
        row["actions"] = actions
        if row["references"] and not evidence:
            row["actions"] = []
            row["impact"] = row["manual"] = ""
        result.append(row)
    return result


def assertion_context(state, *, external=None):
    """Only saved endpoints can establish that an assertion spans power sources."""
    from datetime import datetime

    from .sessions import utc_now

    active = (state or {}).get("session")
    if not active:
        return {}
    seconds = max(
        0, (datetime.fromisoformat(utc_now()) - datetime.fromisoformat(active["started_at"])).total_seconds()
    )
    start = active["start"]["external"]
    transition = (start is False and active["external_seen"]) or (
        start is not None and external is not None and start != external
    )
    return dict(session_seconds=seconds, power_transition=transition)


def assertion_observations(holder, *, session_seconds=None, power_transition=False):
    """Session-relative observations belong to diagnostics, not the conclusion."""
    seconds = holder.get("held_seconds")
    if seconds is None or seconds < 86400 or session_seconds is None or seconds <= session_seconds + 3600:
        return []
    observations = [t("health_view.pre_session")]
    if power_transition:
        observations.append(t("health_view.long_observation"))
    return observations


def assertion_text(holder, *, scenario="away", session_seconds=None, power_transition=False, verbose=False):
    """Describe observed sleep scope without inferring an app's preferences."""
    seconds = holder.get("held_seconds")
    age = (
        t("health_view.held_days", days=int(seconds // 86400))
        if seconds is not None and seconds >= 86400
        else ""
    )
    long_held = bool(seconds and seconds >= 86400)
    key = (
        "idle_long"
        if holder["scope"] == "idle" and long_held
        else "idle"
        if holder["scope"] == "idle"
        else "system"
    )
    text = t("health_view." + key, name=holder["process"], age=age)
    if verbose:
        if holder["scope"] == "idle" and scenario == "work":
            text += " " + t("health_view.idle_work")
        for observation in assertion_observations(
            holder, session_seconds=session_seconds, power_transition=power_transition
        ):
            text += " " + observation
    return text


def presentation(report, *, scenario="away", session_seconds=None):
    """Shared human view; raw evidence and raw status remain in the report.

    state: normal/action/unavailable. advisory marks idle-sleep notices in away.
    summary is the default conclusion; details are verbose-only observations.
    display_actions/action_hint supply at most one default action label. Raw
    actions, evidence and references remain available to verbose/JSON clients.
    """
    from .system.pmset import sleep_blocking_assertions

    context = report.get("assertion_context", {})
    if scenario not in {"work", "away"}:
        scenario = context.get("scenario") or "away"
    if session_seconds is None:
        session_seconds = context.get("session_seconds")
    result = []
    for row in display_checks(report):
        key = row["id"]
        raw = "\n".join(row["evidence"])
        status = row.get("status", "clear")
        state = (
            "action"
            if status == "hit"
            else "unavailable"
            if status in {"unknown", "unavailable"}
            else "normal"
        )
        advisory, details = False, []
        if state == "unavailable":
            reason = row.get("error") or t("health_view.capability_unavailable")
            if row.get("error"):
                from .failures import log_path

                category = (
                    "timeout"
                    if "timeout" in reason.lower() or "deadline" in reason.lower()
                    else "permission"
                    if "denied" in reason.lower() or "permitted" in reason.lower()
                    else "invalid"
                    if "invalid" in reason.lower() or "malformed" in reason.lower()
                    else "read"
                )
                reason = t("health_view.error_" + category, path=log_path())
            conclusion = t("health_view.cannot", title=row["title"], reason=reason)
        elif key == "assertions":
            holders = [h for h in sleep_blocking_assertions(row["evidence"]) if h["pid"]]
            row["holders"] = holders
            advisory = bool(holders) and all(h["scope"] == "idle" for h in holders) and scenario == "away"
            if advisory:
                state = "normal"
            texts = [
                assertion_text(
                    h,
                    scenario=scenario,
                    session_seconds=session_seconds,
                    power_transition=context.get("power_transition", False),
                )
                for h in holders
            ]
            conclusion = texts[0] if texts else t("health_view.assertions_clear")
            details = texts[1:]
            for holder in holders:
                details.extend(
                    assertion_observations(
                        holder,
                        session_seconds=session_seconds,
                        power_transition=context.get("power_transition", False),
                    )
                )
            details = list(dict.fromkeys(details))
        elif key == "brightness" and (m := re.search(r"brightness=([\d.]+); threshold=([\d.]+)", raw)):
            conclusion = t(
                "health_view.brightness_" + ("high" if status == "hit" else "normal"),
                value=round(float(m[1]) * 100),
                threshold=round(float(m[2]) * 100),
            )
        elif key == "high_cpu":
            subjects = row.get("subjects", [])
            if not subjects:
                # Older reports retain enough text to identify non-bundle commands.
                subjects = []
                from pathlib import Path

                for line in row["evidence"]:
                    match = re.search(r"PID \d+: (.+), %CPU=([\d.]+)", line)
                    if match:
                        app = re.search(r"([^/]+)\.app/", match[1])
                        subjects.append(
                            dict(name=app[1] if app else Path(match[1]).name, cpu=float(match[2]))
                        )
            names = [
                t("health_view.cpu_process", name=s["name"] or t("health_view.unknown_process"), cpu=s["cpu"])
                for s in subjects[:3]
            ]
            if not names:
                names = [a["name"] for a in row["actions"] if a.get("name")]
            conclusion = t(
                "health_view.high_cpu_found" if status == "hit" else "health_view.high_cpu_clear",
                names=t("admin.separator").join(names[:3]) or t("health_view.unknown_process"),
            )
        elif key == "keep_awake" and row.get("subjects"):
            from .caffeinate_view import describe, groups

            messages = [describe(group, group.get("scope") or "running") for group in groups(row["subjects"])]
            conclusion = messages[0]
            details.extend(messages[1:])
        elif key == "keep_awake":
            names = list(dict.fromkeys(a["name"] for a in row["actions"] if a.get("name")))
            for line in row["evidence"]:
                app = re.search(r"([^/]+)\.app/", line)
                if app and app[1] not in names:
                    names.append(app[1])
                elif "caffeinate" in line and "caffeinate" not in names:
                    names.append("caffeinate")
            conclusion = t(
                "health_view." + key + ("_found" if status == "hit" else "_clear"),
                names=", ".join(names) or t("health_view.background_process"),
            )
        else:
            suffix = "hit" if status == "hit" else "clear"
            conclusion = t("health_view." + key + "_" + suffix)
            if key == "low_power" and t("health.on_ac") in row["evidence"]:
                conclusion = t("health_view.low_power_ac")
            if key == "brightness":
                conclusion = t("health_view.brightness_ac")
        if row["references"] and not row["evidence"]:
            conclusion = t(
                "health_view.reference", title=row["title"], references=", ".join(row["references"])
            )
            state, advisory = "normal", False
        icon = "?" if state == "unavailable" else "▲" if state == "action" or advisory else "✓"
        display_actions = row["actions"][:1] if state != "unavailable" else []
        hint_key = "caffeinate" if key == "keep_awake" and "caffeinate" in raw else key
        if state == "action" and not display_actions:
            conclusion = t(
                "health_view.with_advice",
                conclusion=conclusion.rstrip(".。"),
                advice=t("health.advice." + hint_key),
            )
        row.update(
            state=state,
            advisory=advisory,
            summary=f"{icon} {conclusion}",
            details=details,
            display_actions=display_actions,
            action_hint=t("health.hint." + hint_key) if state == "action" and not display_actions else "",
        )
        result.append(row)
    return result

"""On-demand provenance from one process snapshot; no observer or polling loop."""

import re
from pathlib import Path

from .i18n import t

WRAPPERS = {"sh", "bash", "zsh", "fish", "env", "node", "python", "python3", "launchd", "sudo"}


def provenance(row, by_pid, command, holders):
    chain, seen = [], {row["pid"]}
    parent = by_pid.get(row["ppid"])
    source = None
    while parent and parent["pid"] not in seen and len(chain) < 32:
        seen.add(parent["pid"])
        identity = {key: parent[key] for key in ("pid", "started", "executable")}
        chain.append(identity)
        executable = parent["executable"]
        app = re.match(r"(.+?\.app)/Contents/", executable)
        # macOS comm can contain a process title (for example CLI arguments).
        # Keep the exact identity for PID safety, but never present those arguments.
        name = Path(app[1]).stem if app else Path(executable).name.split()[0]
        if source is None and name not in WRAPPERS:
            source = dict(name="Claude Code" if name == "claude" else name, **identity)
        parent = by_pid.get(parent["ppid"])
    own = [h for h in holders if str(row["pid"]) == h["pid"]]
    held = min((h["held_seconds"] for h in own if h["held_seconds"] is not None), default=None)
    timeout = re.search(r"(?:^|\s)-t\s+(\d+)(?:\s|$)", command)
    return dict(
        pid=row["pid"],
        started=row["started"],
        source=source,
        parent_chain=chain,
        remaining_seconds=max(0, int(timeout[1]) - held) if timeout and held is not None else None,
        scope="system" if any(h["scope"] == "system" for h in own) else "idle" if own else None,
    )


def describe(subject, scope):
    source = subject["source"]
    name = source["name"] if source else t("caffeinate_view.unknown_source")
    seconds = subject.get("remaining_seconds")
    remaining = (
        t("caffeinate_view.remaining", minutes=max(1, (seconds + 59) // 60)) if seconds is not None else ""
    )
    text = t("caffeinate_view." + (scope if source else "unknown_" + scope), name=name, remaining=remaining)
    if subject.get("restarted") and source:
        text += t("caffeinate_view.restart")
    return text


def groups(subjects, previous=()):
    """Only distinct child identities under the same live parent imply a restart."""
    result = {}
    for subject in subjects:
        source = subject["source"]
        key = (
            (source["pid"], source["started"], source["executable"])
            if source
            else (subject["pid"], subject["started"])
        )
        old = [item for item in previous if source is not None and item.get("source") == source]
        old_children = {(item["pid"], item["started"]) for item in old}
        restarted = any(item.get("restarted") for item in old) or bool(
            old and (subject["pid"], subject["started"]) not in old_children
        )
        if key not in result:
            result[key] = dict(subject, restarted=restarted, members=[])
        result[key]["members"].append(dict(subject, restarted=restarted))
        result[key]["restarted"] |= restarted
        if subject.get("scope") == "system":
            result[key]["scope"] = "system"
    return list(result.values())

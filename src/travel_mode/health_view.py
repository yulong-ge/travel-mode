"""Deduplicated health presentation; raw observations remain intact in JSON."""

import re
from copy import deepcopy


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

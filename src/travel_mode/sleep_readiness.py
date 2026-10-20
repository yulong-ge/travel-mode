"""Finite sleep observations, shared by the plan, session and cheap menu status."""

from pathlib import Path

from .i18n import t


def validate(value):
    if not isinstance(value, dict) or set(value) != {"state", "ready", "items", "observed_at"}:
        raise ValueError("sleep_readiness: invalid fields")
    if value["state"] not in {"blocked", "ready", "idle", "unknown"} or type(value["ready"]) is not bool:
        raise ValueError("sleep_readiness: invalid state")
    from .sessions import timestamp

    timestamp(value["observed_at"])
    if not isinstance(value["items"], list) or any(
        not isinstance(row, dict)
        or row.get("level") not in {"red", "green", "amber", "unknown"}
        or not isinstance(row.get("message"), str)
        for row in value["items"]
    ):
        raise ValueError("sleep_readiness: invalid items")


def evaluate(
    sleep_disabled,
    holders=(),
    *,
    processes=(),
    error=None,
    caffeinate=(),
    caffeinate_actions=(),
    previous=None,
):
    from .failures import log_path
    from .sessions import utc_now
    from .system.health import SYSTEM_OWNERS

    items = []
    if error or sleep_disabled is None:
        items.append(
            dict(
                level="unknown",
                message=t(
                    "redesign.readiness_unknown",
                    reason=t("health_view.error_read", path=log_path())
                    if error
                    else t("health_view.capability_unavailable"),
                ),
                action=None,
            )
        )
    if sleep_disabled == "1":
        message = t("redesign.sleep_disabled")
        # Public product/process identity supplied by the maintainer's field evidence.
        # A helper's presence is evidence of a possible source, not proof of its settings.
        if any(Path(getattr(p, "command", str(p))).name in {"UURemote", "UURemoteHelper"} for p in processes):
            message = t(
                "redesign.readiness_source",
                message=message,
                source=t("redesign.remote_source", name="UURemote"),
            )
        items.append(dict(level="red", message=message, action=dict(kind="sleep")))
    from .caffeinate_view import describe, groups

    caff_holders = [h for h in holders if h["process"] == "caffeinate" and h.get("pid")]
    subjects = list(caffeinate)
    for holder in caff_holders:
        if not any(str(row["pid"]) == holder["pid"] for row in subjects):
            subjects.append(dict(pid=int(holder["pid"]), started=None, source=None, remaining_seconds=None))
    prior = [
        member
        for item in (previous or {}).get("items", [])
        for member in item.get("caffeinate", {}).get("members", [])
    ]
    for group in groups(subjects, prior):
        members = {str(m["pid"]) for m in group["members"]}
        owned = [h for h in caff_holders if h["pid"] in members]
        if not owned:
            continue
        system = any(h["scope"] == "system" for h in owned)
        actions = [a for a in caffeinate_actions if str(a["process"]["pid"]) in members]
        action = dict(actions[0], targets=actions) if len(actions) > 1 else actions[0] if actions else None
        items.append(
            dict(
                level="red" if system else "amber",
                message=describe(group, "system" if system else "idle"),
                action=action,
                holder=dict(owned[0]),
                caffeinate=group,
            )
        )
    for holder in holders:
        if not holder.get("pid") or holder["process"] in SYSTEM_OWNERS:
            continue
        if holder["process"] == "caffeinate":
            continue
        system = holder["scope"] == "system"
        items.append(
            dict(
                level="red" if system else "amber",
                message=t(
                    "redesign.system_holder" if system else "redesign.idle_holder", name=holder["process"]
                ),
                action=None,
                holder=dict(holder),
            )
        )
    if not items:
        items.append(dict(level="green", message=t("redesign.ready"), action=None))
    state = (
        "blocked"
        if any(r["level"] == "red" for r in items)
        else "unknown"
        if any(r["level"] == "unknown" for r in items)
        else "idle"
        if any(r["level"] == "amber" for r in items)
        else "ready"
    )
    return dict(state=state, ready=state in {"ready", "idle"}, items=items, observed_at=utc_now())


def from_report(report):
    """Reuse a completed health scan; no additional system commands."""
    from .system.pmset import sleep_blocking_assertions

    sleep = next(row for row in report["checks"] if row["id"] == "sleep_disabled")
    assertions = next(row for row in report["checks"] if row["id"] == "assertions")
    keep_awake = next((row for row in report["checks"] if row["id"] == "keep_awake"), {})
    value = (
        None if sleep["status"] in {"unknown", "unavailable"} else "1" if sleep["status"] == "hit" else "0"
    )
    return evaluate(
        value,
        sleep_blocking_assertions(assertions["evidence"]),
        error=sleep["error"] or assertions["error"],
        caffeinate=keep_awake.get("subjects", ()),
        caffeinate_actions=[a for a in keep_awake.get("actions", ()) if a["kind"] == "caffeinate"],
        previous=cached(),
    )


def menu_observation(raw, snapshot, *, observation=None):
    """One current global-switch read plus explicitly dated cached assertions."""
    from .system.pmset import parse_sleep_disabled

    result = evaluate(parse_sleep_disabled(raw))
    cached = observation
    result["assertions_observed_at"] = cached["observed_at"] if cached else None
    if cached:
        assertions = [
            dict(item, cached=True, observed_at=cached["observed_at"])
            for item in cached["items"]
            if item.get("holder")
        ]
        if assertions:
            result["items"] = [item for item in result["items"] if item["level"] != "green"]
        result["items"].extend(assertions)
        if any(item["level"] == "red" for item in assertions):
            result.update(state="blocked", ready=False)
    if cached and result["state"] == "ready" and any(item["level"] == "amber" for item in result["items"]):
        result["state"] = "idle"
    if not cached and result["state"] != "blocked":
        result.update(
            state="unknown",
            ready=False,
            items=[dict(level="unknown", message=t("redesign.assertions_not_checked"), action=None)],
        )
    return result


def cache_path():
    from .state import _path

    return _path().parent / "sleep-readiness.json"


def store(value):
    """Replace one bounded local observation, never an activity history or monitor."""
    import json
    import os
    import tempfile

    from .state import StateError

    validate(value)
    path = cache_path()
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(payload.encode()) > 262144:
        raise StateError(f"{path}: sleep_readiness: observation exceeds 256 KiB")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=".sleep-ready-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except OSError as exc:
        raise StateError(f"{path}: sleep_readiness: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def cached():
    import json

    from .state import StateError

    path = cache_path()
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 262144:
            raise ValueError("observation exceeds 256 KiB")
        result = json.loads(path.read_text())
        validate(result)
        return result
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise StateError(f"{path}: sleep_readiness: {exc}") from exc

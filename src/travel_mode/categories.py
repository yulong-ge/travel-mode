"""Finite, data-driven identification; discovery never starts a background task."""

from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache
from importlib.resources import files

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

CATEGORIES = ("sync", "backup", "updater", "vm", "keep-awake")


@lru_cache(maxsize=1)
def rules() -> tuple[dict, ...]:
    data = tomllib.loads(files("travel_mode").joinpath("categories.toml").read_text("utf-8"))
    rows = data["rules"]
    seen = set()
    for row in rows:
        if row["id"] in seen or row["category"] not in CATEGORIES:
            raise ValueError(f"Invalid category rule: {row['id']}")
        if row["action"] not in {"unload", "quit", "freeze", "notice"}:
            raise ValueError(f"Invalid category action: {row['id']}")
        seen.add(row["id"])
    return tuple(rows)


def identify(*, label="", bundle_id="", name="", executable="") -> dict | None:
    for row in rules():
        for field, value in (
            ("labels", label),
            ("bundle_ids", bundle_id),
            ("apps", name),
            ("executables", executable),
        ):
            if value and any(fnmatchcase(value.casefold(), pattern.casefold()) for pattern in row[field]):
                return row
    return None


def metadata(rule: dict | None) -> dict:
    return (
        {"category": rule["category"], "rule_id": rule["id"], "category_action": rule["action"]}
        if rule
        else {}
    )


def ignored(rule: dict, config) -> bool:
    return bool(
        {rule["id"].casefold(), rule["name"].casefold()}
        & {value.casefold() for value in config.categories.ignore}
    )


def protected(rule: dict, config) -> bool:
    names = {value.casefold(): value for value in (*rule["apps"], rule["name"])}
    ignored_names = {value.casefold() for value in config.apps.ignore}
    scoped = (
        getattr(config.apps, config.options.scenario, {})
        if config.options.scenario in {"work", "away"}
        else {}
    )
    skipped = {value.casefold() for value in config.apps.skip if scoped.get(value, "skip") == "skip"}
    skipped.update(value.casefold() for value, action in scoped.items() if action == "skip")
    return bool(names.keys() & (ignored_names | skipped))


def selected(rule: dict, config) -> bool:
    return (
        config.options.scenario == "work"
        and rule["category"] in config.categories.pause
        and not ignored(rule, config)
        and not protected(rule, config)
        and rule["action"] != "notice"
    )


def app_action(name: str, rule: dict | None, config) -> str:
    explicit = config.apps.explicit(name, config.options.scenario)
    if name in config.apps.ignore:
        return "skip"
    if explicit is not None:
        return explicit
    if config.apps.policy == "adaptive":
        from .app_policy import recommend

        if rule and (rule["action"] == "notice" or ignored(rule, config)):
            return "skip"
        recommendation = recommend(name, rule=rule)
        if recommendation["reason"] == "freeze":
            return config.apps.default
        return recommendation[
            config.options.scenario if config.options.scenario in {"work", "away"} else "work"
        ]
    if name in config.apps.skip:
        return "skip"
    if rule and protected(rule, config):
        return "skip"
    if name in config.apps.configured_actions:
        return config.apps.actions[name]
    if rule and rule["action"] == "notice":
        return "skip"
    if rule and ignored(rule, config) and name not in config.apps.configured_actions:
        return "skip"
    if config.options.scenario == "work" and name not in config.apps.configured_actions:
        if rule and selected(rule, config) and rule["action"] in {"quit", "freeze"}:
            return rule["action"]
        return config.apps.default
    if name in config.apps.actions:
        return config.apps.actions[name]
    if rule and selected(rule, config) and rule["action"] in {"quit", "freeze"}:
        return rule["action"]
    return config.apps.default


def grouped(rows):
    """Stable order within categories; preserve caller row indices separately."""
    return sorted(
        rows,
        key=lambda row: (
            CATEGORIES.index(row["category"]) if row.get("category") in CATEGORIES else len(CATEGORIES)
        ),
    )


def pause_categories(categories, app_rows, service_rows, execute, save, state):
    """One admitted batch; deduplicate identities and respect explicit exclusions."""
    from .actions.apps import FreezeApp, QuitApp, UnloadAgent
    from .config import load_config
    from .system.launchd import match_services

    config = load_config()
    seen = set()
    for row in service_rows:
        rule = identify(label=row["label"], executable=row.get("executable", ""))
        if (
            not rule
            or rule["category"] not in categories
            or ignored(rule, config)
            or protected(rule, config)
            or rule["action"] == "notice"
            or row["label"].startswith("com.apple.")
            or any(match_services(selector, [row]) for selector in config.services.keep)
            or row["plist_path"] in seen
            or row["plist_path"] in state.get("unloaded_svcs", [])
        ):
            continue
        seen.add(row["plist_path"])
        _, rc = execute(UnloadAgent(row["plist_path"]))
        if rc:
            continue
        save()
    for row in app_rows:
        rule = identify(name=row["name"], bundle_id=row.get("bundle_id", ""))
        if (
            not rule
            or rule["category"] not in categories
            or ignored(rule, config)
            or protected(rule, config)
            or rule["action"] not in {"quit", "freeze"}
            or app_action(row["name"], rule, config) in {"skip", "ask"}
            or row["path"] in seen
            or row["path"] in (*state.get("quit_apps", []), *state.get("frozen_apps", []))
        ):
            continue
        seen.add(row["path"])
        action = (
            row["defact"]
            if config.apps.policy == "adaptive" and "defact" in row
            else app_action(row["name"], rule, config)
        )
        if action not in {"quit", "freeze"}:
            continue
        operation = FreezeApp(row["path"]) if action == "freeze" else QuitApp(row["path"], row["name"])
        if not execute(operation):
            continue
        save()

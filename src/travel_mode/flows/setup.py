"""Explicit configuration edits shared by the CLI and the finite setup screen."""

from __future__ import annotations

import os
import sys

from .. import app_policy, onboarding
from ..config import load_config, preview_app_choices, write_app_choices
from ..i18n import t
from ..paths import config_path


def failure(exc):
    from ..failures import log_path, message, record
    from ..state import StateError

    # Configuration edits do not create a power-saving session or recovery snapshot.
    try:
        row = record(
            None, t("setup.title"), str(exc), getattr(exc, "setup_commands", ()), error=exc, store=False
        )
    except StateError as log_error:
        if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
            raise exc from log_error
        return t("failure.logging_failed", reason=exc, logging=log_error, path=log_path())
    if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
        raise exc
    return message(row)


def save(selected, *, completed=False, adaptive=False, dry_run=False):
    if dry_run:
        return preview_app_choices(selected, completed=completed, adaptive=adaptive)
    write_app_choices(selected, completed=completed, adaptive=adaptive)
    return t("setup.saved")


def run(*, yes=False, dry_run=False, input_fn=None):
    ask = input_fn or input
    try:
        if not yes and not dry_run:
            answer = ask(t("setup.begin")).strip().lower()
            if answer == "s":
                print(save({}, completed=True, adaptive=True))
                return 0
            if answer:
                raise ValueError(t("setup.begin_invalid"))
        data = onboarding.scan()
        rows = data["apps"]
        selected = onboarding.choices(data, load_config())
        print(onboarding.detected(data))
        print(t("setup.title"))
        for group in onboarding.GROUPS:
            members = [row for row in rows if row["group"] == group]
            services = [row for row in data["services"] if row["group"] == group]
            if not members and not services:
                continue
            print(t("onboarding.group_" + group))
            for row in members:
                print(app_policy.row_text(row, selected))
                if not yes and not dry_run:
                    for scenario in ("work", "away"):
                        answer = (
                            ask(
                                t(
                                    "setup.choose",
                                    name=row["name"],
                                    scenario=t("scenario." + scenario),
                                    action=selected[scenario][row["name"]],
                                )
                            )
                            .strip()
                            .lower()
                        )
                        if answer:
                            if answer not in {"freeze", "quit", "ask", "skip"}:
                                raise ValueError(t("config.invalid_action"))
                            selected[scenario][row["name"]] = answer
            for row in services:
                label = row["label"]
                print(
                    t(
                        "onboarding.service",
                        name=label,
                        action=t("onboarding." + selected["services"][label]),
                        reason=onboarding.reason(row),
                    )
                )
                if not yes and not dry_run:
                    answer = ask(t("onboarding.choose_service", name=label)).strip().lower()
                    if answer:
                        if answer not in {"keep", "unload"}:
                            raise ValueError(t("config.invalid_action"))
                        selected["services"][label] = answer
        for row in data["readiness"]["items"]:
            print(row["message"])
        print(onboarding.summary(selected, config_path()))
        if dry_run:
            print(save(selected, completed=True, adaptive=True, dry_run=True), end="")
        elif yes or ask(t("setup.confirm")).strip().lower() == "y":
            print(save(selected, completed=True, adaptive=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - UI boundary logs and explicitly presents every failure
        print(failure(exc), file=sys.stderr)
        return 1


class Remember:
    """One question per app/scenario for this open CLI/TUI session."""

    def __init__(self):
        self.asked = set()

    def offer(self, name, scenario, action, default):
        key = (name, scenario)
        if action == default or key in self.asked or action not in {"freeze", "quit", "ask", "skip"}:
            return False
        self.asked.add(key)
        return True

    def prompt(self, name, scenario, action):
        return t(
            "setup.remember", scenario=t("scenario." + scenario), action=t("action." + action), name=name
        )

    def answer(self, name, scenario, action, accepted, *, dry_run=False):
        if accepted:
            return save({scenario: {name: action}}, dry_run=dry_run)
        return None

    def cli(self, name, scenario, action, default, *, input_fn=None, dry_run=False):
        if dry_run or not self.offer(name, scenario, action, default):
            return
        try:
            accepted = (input_fn or input)(self.prompt(name, scenario, action)).strip().lower() == "y"
            text = self.answer(name, scenario, action, accepted)
            if text:
                print(text)
        except Exception as exc:  # noqa: BLE001 - UI boundary logs and explicitly presents every failure
            print(failure(exc), file=sys.stderr)

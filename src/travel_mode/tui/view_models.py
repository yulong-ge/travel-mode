"""Widget-free content contracts for the existing pages.

No layout, colour or widget references belong in these view models.
"""

from travel_mode.i18n import t


def app_row(app, *, state="running", failure=None):
    operations = {
        "running": ["quit", "freeze", "skip"],
        "refused": ["retry_quit", "force_quit", "freeze", "skip"],
        "error": ["quit", "freeze", "skip"],
        "exited": [],
        "frozen": ["thaw"],
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

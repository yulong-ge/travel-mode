"""Operate the canonical plan without depending on sort indices or obsolete tabs."""

import asyncio

from tui_interaction import click_after_layout


def app_item(app, name):
    return next(row for row in app.plan_items if row["descriptor"]["kind"] == "app" and row["name"] == name)


async def select_plan(app, *, group=None, names=None, ids=None, action=None):
    if app.view_mode == "result":
        await app.utility_action("b-new-plan")
        await app.screen.choose_scenario(app.scenario)
        await asyncio.wait_for(app.new_plan_complete.wait(), 30)
        await app.wait_for_idle()
        app._session_admitted = True
        await app.wait_for_idle()
    app.view_mode = "plan"
    for row in app.plan_items:
        row["selected"] = row["state"] == "planned" and (
            row["group"] == group
            if group
            else row["name"] in names
            if names is not None
            else row["id"] in ids
            if ids is not None
            else True
        )
        if row["selected"] and action and row["descriptor"]["kind"] == "app":
            row["action"] = action
    app.main_query_one("#q-measure").value = False
    app.active_filter = "all"
    await app.render_plan()
    assert await app.screen.wait_for_refresh()


async def apply_plan(pilot, **selection):
    app = pilot.app
    await app.wait_for_idle()
    await select_plan(app, **selection)
    await click_after_layout(pilot, "#b-quick")
    await app.wait_for_idle()
    assert await app.screen.wait_for_refresh()

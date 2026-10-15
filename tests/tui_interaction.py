"""Mouse admission after committed layout, without timed sleeps or click retries."""

import asyncio

from textual.widget import Widget


async def click_after_layout(pilot, selector):
    app = pilot.app
    generation = app._refresh_generation
    assert app.refresh_complete
    # Data workers finish before Textual commits their resulting layout.
    assert await asyncio.wait_for(app.screen.wait_for_refresh(), timeout=5)
    button = app.query_one(selector)
    assert button.is_attached and button.display and not button.disabled
    button.scroll_visible(animate=False, immediate=True)
    # Scrolling also needs a committed compositor update before hit testing.
    assert await asyncio.wait_for(app.screen.wait_for_refresh(), timeout=5)
    assert app.refresh_complete and app._refresh_generation == generation
    assert app.query_one(selector) is button
    visible = button.region.intersection(app.screen.region)
    for parent in button.ancestors:
        if isinstance(parent, Widget):
            visible = visible.intersection(parent.content_region)
    assert button.region.area and visible == button.region, (selector, button.region, visible)
    offset = button.region.width // 2, button.region.height // 2
    target, _ = app.get_widget_at(button.region.x + offset[0], button.region.y + offset[1])
    assert target is button, (selector, target)
    assert await pilot.click(button, offset=offset)

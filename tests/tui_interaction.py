"""Mouse admission after committed layout, without timed sleeps or click retries."""

import asyncio
from collections import defaultdict

from textual.widget import Widget

from travel_mode.tui.messages import PageRefreshed


class PageSignals:
    """run_test message hook for tests that deliberately keep other pages pending."""

    def __init__(self):
        self.completed = defaultdict(asyncio.Event)

    def __call__(self, message):
        if isinstance(message, PageRefreshed):
            self.completed[message.page].set()

    async def wait(self, *pages):
        await asyncio.wait_for(asyncio.gather(*(self.completed[page].wait() for page in pages)), 30)


async def click_after_layout(pilot, selector):
    app = pilot.app
    await asyncio.wait_for(app.wait_for_idle(), timeout=30)

    generation = app._refresh_generation
    assert app.refresh_complete
    # Data workers finish before Textual commits their resulting layout.
    assert await asyncio.wait_for(app.screen.wait_for_refresh(), timeout=30)
    button = app.query_one(selector)
    assert button.is_attached and button.display and not button.disabled
    # A previous focus request can still have an animated scroll queued. Move
    # focus first, then wait for the actual scroll completion rather than a frame
    # that may precede it. The mouse click below remains a single hit-tested click.
    button.focus(scroll_visible=False)
    await asyncio.wait_for(app.animator.wait_for_idle(), timeout=30)
    scrolled = asyncio.Event()
    button.scroll_visible(animate=False, immediate=True, on_complete=scrolled.set)
    await asyncio.wait_for(scrolled.wait(), timeout=30)
    # Scrolling also needs a committed compositor update before hit testing.
    assert await asyncio.wait_for(app.screen.wait_for_refresh(), timeout=30)
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
    button.dispatch_posted.clear()
    assert await pilot.click(button, offset=offset)
    await asyncio.wait_for(button.dispatch_posted.wait(), timeout=30)
    await asyncio.wait_for(asyncio.shield(button.last_dispatch), timeout=30)
    await asyncio.wait_for(app.wait_for_idle(), timeout=30)
    assert await asyncio.wait_for(app.screen.wait_for_refresh(), timeout=30)


async def toggle_language(pilot):
    app = pilot.app
    app.language_posted.clear()
    await pilot.press("l")
    await asyncio.wait_for(app.language_posted.wait(), timeout=30)
    await asyncio.wait_for(asyncio.shield(app.last_language_dispatch), timeout=30)
    await asyncio.wait_for(app.wait_for_idle(), timeout=30)
    assert await asyncio.wait_for(app.screen.wait_for_refresh(), timeout=30)

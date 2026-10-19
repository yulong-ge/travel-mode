"""Shared Textual widgets."""

import asyncio

from rich.text import Text
from textual.widgets import Button as TextualButton
from textual.widgets import Static


class Button(TextualButton):
    """One admission per click sequence, including already queued presses."""

    _mutation_done = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dispatch_posted = asyncio.Event()
        self.last_dispatch = None

    async def _on_click(self, event):
        event.stop()
        event.prevent_default()
        if event.chain == 1:
            self.press()

    def post_message(self, message):
        if isinstance(message, TextualButton.Pressed):
            message.travel_generation = self.app._mutation_generation
            message.travel_completion = self.app.track_input()
            self.last_dispatch = message.travel_completion
            self.dispatch_posted.set()
        posted = super().post_message(message)
        if not posted and isinstance(message, TextualButton.Pressed):
            self.app.complete_input(message.travel_completion)
        return posted

    def on_mount(self):
        self.app._disable_mutation_button(self)


class ActionFooter(Static):
    """Keep the complete message; truncate only the single visible line."""

    def full_text(self):
        return self.content.copy() if isinstance(self.content, Text) else Text.from_markup(str(self.content))

    @property
    def truncated(self):
        return self.full_text().cell_len > self.content_region.width

    def render(self):
        text = self.full_text()
        text.truncate(self.content_region.width, overflow="ellipsis")
        return text

    def on_resize(self):
        self.refresh()
        self.app.refresh_detail_log()

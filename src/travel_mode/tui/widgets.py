"""Shared Textual widgets."""

import asyncio
import unicodedata

from textual.widgets import Button as TextualButton
from textual.widgets import TabbedContent


def _ellip(s, limit=86):
    """按显示宽度截断 (CJK 计 2)."""
    w = 0
    for i, ch in enumerate(s):
        w += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if w > limit:
            return s[:i] + "…"
    return s


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


class TravelTabs(TabbedContent):
    def _on_tab_pane_focused(self, event):
        # 挂载/移除控件会排队 Focused 消息。旧页面的消息不得切回该页。
        event.prevent_default()
        event.stop()
        if event.tab_pane.id != self.active:
            focused = self.app.focused
            if focused is not None and event.tab_pane in focused.ancestors:
                self.focus()


def status_row(text, *, icon="", button=None, compact=False):
    """Reserve the same two cells for every status icon, including blank ones."""
    from textual.containers import Horizontal
    from textual.widgets import Label

    children = [Label(icon, classes="status-icon"), Label(text, classes="grow")]
    if button is not None:
        children.append(button)
    return Horizontal(*children, classes="status-line" if compact else "row")

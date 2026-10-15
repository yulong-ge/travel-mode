"""逐页原子回填完成的观测事件。"""

from textual.message import Message


class PageRefreshed(Message):
    def __init__(self, page: str):
        self.page = page
        super().__init__()

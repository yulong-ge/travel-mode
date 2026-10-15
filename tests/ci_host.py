"""Opt-in Linux boundary simulation: PYTHONPATH=tests uv run pytest -p ci_host.

Only the product's platform view changes. Altering global sys.platform on a
macOS interpreter would also change asyncio, sysconfig and Pillow internals.
Tests of macOS behavior must request darwin_platform and install their own font.
"""

import sys

import pytest
from PIL import ImageFont

from travel_mode.system import platform


class LinuxSystem:
    platform = "linux"

    def __getattr__(self, name):
        return getattr(sys, name)


@pytest.fixture(autouse=True)
def ci_host(monkeypatch):
    monkeypatch.setattr(platform, "sys", LinuxSystem())
    truetype = ImageFont.truetype

    def linux_font(font, *args, **kwargs):
        if isinstance(font, str) and font.startswith("/System/Library/Fonts/"):
            raise OSError(f"cannot open resource: {font}")
        return truetype(font, *args, **kwargs)

    monkeypatch.setattr(ImageFont, "truetype", linux_font)

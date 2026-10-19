"""Render tmux SGR captures on a terminal cell grid (no proportional advance)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SGR = re.compile(r"\x1b\[([0-9;:]*)m")
DEFAULT_FG = (224, 224, 224)
DEFAULT_BG = (18, 18, 18)
PALETTE = (
    (0, 0, 0),
    (205, 49, 49),
    (13, 188, 121),
    (229, 229, 16),
    (36, 114, 200),
    (188, 63, 188),
    (17, 168, 205),
    (229, 229, 229),
    (102, 102, 102),
    (241, 76, 76),
    (35, 209, 139),
    (245, 245, 67),
    (59, 142, 234),
    (214, 112, 214),
    (41, 184, 219),
    (255, 255, 255),
)
CELL_W, CELL_H = 11, 24


def cell_width(char: str) -> int:
    if unicodedata.combining(char) or char in {"\ufe0f", "\u200d"}:
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def text_width(text: str) -> int:
    return sum(cell_width(char) for char in text)


def font_family(char: str) -> str:
    code = ord(char)
    if 0x2E80 <= code <= 0xA4CF or 0xF900 <= code <= 0xFAFF or 0xFF00 <= code <= 0xFFEF or code >= 0x20000:
        return "cjk"
    if 0x2190 <= code <= 0x21FF or char in "✓✔✗✘▲○":
        return "symbol"
    return "mono"


def indexed_color(index: int) -> tuple[int, int, int]:
    if not 0 <= index <= 255:
        raise ValueError(f"Invalid ANSI palette index: {index}")
    if index < 16:
        return PALETTE[index]
    if index < 232:
        index -= 16
        levels = (0, 95, 135, 175, 215, 255)
        return levels[index // 36], levels[index // 6 % 6], levels[index % 6]
    level = 8 + 10 * (index - 232)
    return level, level, level


@dataclass(frozen=True)
class Style:
    fg: tuple[int, int, int] = DEFAULT_FG
    bg: tuple[int, int, int] = DEFAULT_BG
    bold: bool = False
    reverse: bool = False
    dim: bool = False
    underline: bool = False


def apply_sgr(style: Style, parameters: str) -> Style:
    codes = [int(value) if value else 0 for value in parameters.replace(":", ";").split(";")]
    index = 0
    while index < len(codes):
        code = codes[index]
        if code == 0:
            style = Style()
        elif code in (1, 2, 4, 7, 22, 24, 27):
            if code == 22:
                style = replace(style, bold=False, dim=False)
            else:
                field = {1: "bold", 2: "dim", 4: "underline", 7: "reverse", 24: "underline", 27: "reverse"}[
                    code
                ]
                style = replace(style, **{field: code not in (24, 27)})
        elif code in (39, 49):
            style = replace(style, **{"fg" if code == 39 else "bg": DEFAULT_FG if code == 39 else DEFAULT_BG})
        elif 30 <= code <= 37 or 90 <= code <= 97:
            style = replace(style, fg=PALETTE[code - (30 if code < 90 else 82)])
        elif 40 <= code <= 47 or 100 <= code <= 107:
            style = replace(style, bg=PALETTE[code - (40 if code < 100 else 92)])
        elif code in (38, 48):
            mode = codes[index + 1]
            if mode == 5:
                color = indexed_color(codes[index + 2])
                index += 2
            elif mode == 2:
                color = tuple(codes[index + 2 : index + 5])
                if len(color) != 3 or any(not 0 <= value <= 255 for value in color):
                    raise ValueError(f"Invalid ANSI RGB sequence: {parameters}")
                index += 4
            else:
                raise ValueError(f"Unsupported ANSI color mode: {parameters}")
            style = replace(style, **{"fg" if code == 38 else "bg": color})
        index += 1
    return style


def styled_cells(text: str, *, cols: int | None = None):
    """Yield (row, column, character, width, style), preserving SGR across lines."""
    style, row, column, index = Style(), 0, 0, 0
    while index < len(text):
        match = SGR.match(text, index)
        if match:
            style = apply_sgr(style, match[1])
            index = match.end()
            continue
        char = text[index]
        index += 1
        if char == "\n":
            # tmux capture-pane omits trailing blanks but preserves their SGR background.
            # Paint those terminal cells too, including the last button's right padding.
            if cols is not None:
                for trailing in range(column, cols):
                    yield row, trailing, " ", 1, style
            row, column = row + 1, 0
        elif char == "\r":
            column = 0
        elif char == "\x1b":
            raise ValueError(f"Unsupported terminal escape at offset {index - 1}")
        else:
            width = cell_width(char)
            yield row, column, char, width, style
            column += width
    if cols is not None and column:
        for trailing in range(column, cols):
            yield row, trailing, " ", 1, style


def render(text: str, path: Path, cols: int = 120, rows: int = 36) -> None:
    fonts = {
        "mono": ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 18),
        "cjk": ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 19),
        "symbol": ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 18),
    }
    bold_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 18, index=1)
    image = Image.new("RGB", (cols * CELL_W, rows * CELL_H), DEFAULT_BG)
    draw = ImageDraw.Draw(image)
    cells = list(styled_cells(text, cols=cols))
    # Paint backgrounds first so a following cell cannot erase a glyph's right edge.
    for row, column, _char, width, style in cells:
        if row >= rows or column >= cols:
            continue
        if column + width > cols:
            raise ValueError(f"Capture exceeds {cols} columns at row {row + 1}")
        bg = style.fg if style.reverse else style.bg
        x, y = column * CELL_W, row * CELL_H
        if width:
            draw.rectangle((x, y, x + width * CELL_W - 1, y + CELL_H - 1), fill=bg)
    for row, column, char, _width, style in cells:
        if row >= rows or column >= cols:
            continue
        fg, bg = (style.bg, style.fg) if style.reverse else (style.fg, style.bg)
        if style.dim:
            fg = tuple((f + b) // 2 for f, b in zip(fg, bg, strict=True))
        x, y = column * CELL_W, row * CELL_H
        family = font_family(char)
        font = bold_font if style.bold and family == "mono" else fonts[family]
        # All fonts share a baseline; glyph width never changes terminal positioning.
        draw.text((x, y + 18), char, font=font, fill=fg, anchor="ls", stroke_width=0)
        if style.underline:
            draw.line((x, y + 21, x + _width * CELL_W - 1, y + 21), fill=fg)
    from harness_evidence import atomic_output

    with atomic_output(path) as temporary:
        image.save(temporary)

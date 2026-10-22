"""Character / string width helpers.

Terminals are a grid of *cells*, and not every character takes exactly one
cell: CJK characters and most emoji take two, combining accents take zero.
The renderer needs those numbers to be right or text drifts out of alignment.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

#: Character used for an empty cell. Never written to the terminal.
EMPTY = "\x00"

#: Character used for the second half of a double-width glyph.
CONTINUATION = "\x01"

#: Characters that count as "blank" when compositing layers.
BLANK = (EMPTY, CONTINUATION, " ")

#: Cache of per-character widths; the hot loop below must stay cheap.
_WIDTH_CACHE: dict[str, int] = {}


@lru_cache(maxsize=1)
def _zero_width_categories() -> frozenset[str]:
    return frozenset({"Mn", "Me", "Cf"})


def char_width(ch: str) -> int:
    """Return the number of terminal cells ``ch`` occupies (0, 1 or 2)."""
    try:
        return _WIDTH_CACHE[ch]
    except KeyError:
        pass

    if ch in ("", EMPTY, CONTINUATION):
        w = 0 if ch != "" else 0
    elif ch == "\t":
        w = 4
    elif ch in ("\n", "\r"):
        w = 0
    else:
        cat = unicodedata.category(ch)
        if cat in _zero_width_categories() or cat in ("Cc", "Cs", "Co"):
            w = 0
        elif unicodedata.east_asian_width(ch) in ("W", "F"):
            w = 2
        else:
            w = 1

    if len(_WIDTH_CACHE) < 8192:
        _WIDTH_CACHE[ch] = w
    return w


def text_width(text: str) -> int:
    """Total number of cells ``text`` occupies."""
    if text.isascii():
        return len(text)
    return sum(char_width(c) for c in text)


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    """Cut ``text`` down to at most ``width`` cells, appending ``ellipsis``."""
    if width <= 0:
        return ""
    if text_width(text) <= width:
        return text
    if ellipsis and text_width(ellipsis) > width:
        ellipsis = ""
    limit = width - text_width(ellipsis)
    out: list[str] = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis


def pad(text: str, width: int, align: str = "left", fill: str = " ") -> str:
    """Pad ``text`` with ``fill`` up to ``width`` cells."""
    gap = width - text_width(text)
    if gap <= 0:
        return truncate(text, width, "")
    fill = fill[:1] or " "
    if align == "right":
        return fill * gap + text
    if align == "center":
        left = gap // 2
        return fill * left + text + fill * (gap - left)
    return text + fill * gap


def wrap(text: str, width: int, *, hard: bool = True) -> list[str]:
    """Word-wrap ``text`` into lines of at most ``width`` cells.

    ``hard=True`` splits words that are longer than the line (no overflow).
    Newlines in the source text are always respected.
    """
    if width <= 0:
        return [""]
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            if word == "":
                candidate = current + " " if current else ""
            elif current:
                candidate = current + " " + word
            else:
                candidate = word

            if text_width(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if hard and text_width(word) > width:
                for chunk in _split_hard(word, width):
                    lines.append(chunk)
            elif word:
                current = word
        if current or not lines:
            lines.append(current)
    return lines or [""]


def _split_hard(word: str, width: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    used = 0
    for ch in word:
        w = char_width(ch)
        if used + w > width:
            chunks.append(current)
            current, used = "", 0
        current += ch
        used += w
    if current:
        chunks.append(current)
    return chunks


def cell_slice(text: str, start: int, stop: int) -> str:
    """Slice ``text`` by *cell offset* rather than by character index."""
    out: list[str] = []
    pos = 0
    for ch in text:
        w = char_width(ch)
        if pos >= stop:
            break
        if pos + w > start:
            out.append(ch)
        pos += w
    return "".join(out)


__all__ = [
    "BLANK",
    "CONTINUATION",
    "EMPTY",
    "cell_slice",
    "char_width",
    "pad",
    "text_width",
    "truncate",
    "wrap",
]

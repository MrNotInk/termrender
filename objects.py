"""Text objects — the things you *put*, *locate*, *move* and *remove*."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from .style import Style, normalize_style
from .text import char_width, text_width

if TYPE_CHECKING:  # pragma: no cover
    from .layer import Layer

Run = tuple[str, Style]


def split_runs(runs: Sequence[Run], width: int | None) -> list[list[Run]]:
    """Split styled runs into lines of at most ``width`` cells.

    Greedy word wrap: lines break between words, spaces at a break are
    dropped, and a word longer than the line is hard-split. Per-character
    styles survive the split, so a wrapped sentence keeps its colours.
    ``width=None`` only honours explicit newlines.
    """
    if width is None:  # no wrapping: only explicit newlines matter
        lines: list[list[Run]] = [[]]
        for text, style in runs:
            for ch in text:
                if ch == "\r":
                    continue
                if ch == "\n":
                    lines.append([])
                    continue
                if lines[-1] and lines[-1][-1][1] == style:
                    lines[-1][-1] = (lines[-1][-1][0] + ch, style)
                else:
                    lines[-1].append((ch, style))
        return lines

    glyphs: list[tuple[str, Style]] = []
    for text, style in runs:
        for ch in text:
            if ch != "\r":
                glyphs.append((ch, style))

    def merge(chunk: Sequence[tuple[str, Style]]) -> list[Run]:
        merged: list[Run] = []
        for ch, style in chunk:
            if merged and merged[-1][1] == style:
                merged[-1] = (merged[-1][0] + ch, style)
            else:
                merged.append((ch, style))
        return merged

    lines: list[list[Run]] = []
    current: list[tuple[str, Style]] = []
    used = 0
    pending_space: list[tuple[str, Style]] = []

    def flush() -> None:
        nonlocal current, used, pending_space
        lines.append(merge(current))
        current, used, pending_space = [], 0, []

    for token, is_word in _tokenize(glyphs):
        if token == "\n":  # explicit newline
            flush()
            continue
        if not is_word:
            if current:  # spaces only matter between words
                pending_space = token
            continue

        word_width = sum(char_width(c) for c, _ in token)
        gap = sum(char_width(c) for c, _ in pending_space)
        if current and used + gap + word_width > width:
            flush()
            gap = 0
        if current and gap:
            current.extend(pending_space)
            used += gap
        pending_space = []

        if word_width > width and used == 0:
            for chunk in _hard_split(token, width):
                if current:
                    flush()
                current = list(chunk)
                used = sum(char_width(c) for c, _ in current)
            continue

        current.extend(token)
        used += word_width

    flush()
    return lines or [[]]


def _tokenize(glyphs: Sequence[tuple[str, Style]]):
    """Yield ``(token, is_word)`` pairs; newlines come through as ``"\n"``."""
    index = 0
    total = len(glyphs)
    while index < total:
        ch, _ = glyphs[index]
        if ch == "\n":
            yield ch, False
            index += 1
            continue
        is_word = ch != " "
        start = index
        while index < total:
            candidate, _ = glyphs[index]
            if candidate == "\n" or (candidate == " ") != (not is_word):
                break
            index += 1
        yield glyphs[start:index], is_word


def _hard_split(token: Sequence[tuple[str, Style]], width: int) -> list[list[tuple[str, Style]]]:
    chunks: list[list[tuple[str, Style]]] = []
    current: list[tuple[str, Style]] = []
    used = 0
    for ch, style in token:
        w = char_width(ch)
        if used + w > width and current:
            chunks.append(current)
            current, used = [], 0
        current.append((ch, style))
        used += w
    if current:
        chunks.append(current)
    return chunks


class TextObject:
    """A named piece of text pinned to an ``(x, y)`` position on a layer.

    Objects are the unit of *removal*: unlike raw drawing, an object knows
    which cells it owns, so it can be moved, re-styled or deleted without
    touching anything else on screen. Every object has an ``id`` (generated
    if you do not supply one) so you can also fetch it later with
    ``layer[id]`` / ``layer.object(id)``.

    Text objects are live handles — mutating one marks its layer dirty and
    the change appears on the next :meth:`~termrender.screen.Screen.draw`.
    """

    __slots__ = (
        "align",
        "clip",
        "height",
        "id",
        "layer",
        "visible",
        "wrap",
        "x",
        "y",
        "z",
        "_runs",
    )

    def __init__(
        self,
        layer: Layer,
        obj_id: str,
        x: int,
        y: int,
        runs: Sequence[Run],
        *,
        visible: bool = True,
        clip: bool = True,
        wrap: int | None = None,
        align: str = "left",
        z: int = 0,
    ) -> None:
        self.layer = layer
        self.id = obj_id
        self.x = int(x)
        self.y = int(y)
        self._runs: list[Run] = list(runs)
        self.visible = visible
        self.clip = clip
        self.wrap = wrap
        self.align = align
        self.z = z
        self.height = 1

    # -- content ---------------------------------------------------------
    @property
    def text(self) -> str:
        return "".join(run[0] for run in self._runs)

    @property
    def runs(self) -> list[Run]:
        return list(self._runs)

    @property
    def style(self) -> Style:
        return self._runs[0][1] if self._runs else Style()

    @property
    def width(self) -> int:
        """Widest line, in cells."""
        return max((text_width(line_text) for line_text in self.lines_text()), default=0)

    def set(self, content: str, style: Style | dict | None = None, /, **kwargs: object) -> TextObject:
        """Replace the text (and optionally the style). Returns ``self``."""
        st = normalize_style(style, base=self.style if style is None and not kwargs else None, **kwargs)
        self._runs = [(content, st)] if content else []
        return self._touch()

    def set_runs(self, runs: Iterable[tuple[str, Style | dict | None]]) -> TextObject:
        """Replace the content with individually styled runs."""
        self._runs = [(t, normalize_style(s)) for t, s in runs if t]
        return self._touch()

    def append(self, content: str, style: Style | dict | None = None, /, **kwargs: object) -> TextObject:
        """Add text to the end of the object."""
        st = normalize_style(style, base=self.style, **kwargs)
        if self._runs and self._runs[-1][1] == st:
            self._runs[-1] = (self._runs[-1][0] + content, st)
        else:
            self._runs.append((content, st))
        return self._touch()

    def style_with(self, style: Style | dict | None = None, /, **kwargs: object) -> TextObject:
        """Re-style the whole object in place, keeping the text."""
        st = normalize_style(style, **kwargs)
        self._runs = [(t, st) for t, _ in self._runs]
        return self._touch()

    # -- geometry --------------------------------------------------------
    def move(self, x: int, y: int) -> TextObject:
        """Reposition the object. Returns ``self`` for chaining."""
        self.x, self.y = int(x), int(y)
        return self._touch()

    def shift(self, dx: int = 0, dy: int = 0) -> TextObject:
        """Move relative to the current position."""
        self.x += int(dx)
        self.y += int(dy)
        return self._touch()

    def center(self, x: int | None = None, y: int | None = None) -> TextObject:
        """Center the object on ``(x, y)`` (defaults to the layer centre)."""
        cx = self.layer.width // 2 if x is None else x
        cy = self.layer.height // 2 if y is None else y
        return self.move(cx - self.width // 2, cy - self.lines // 2)

    def resize(self, wrap: int | None) -> TextObject:
        """Change the wrap width (``None`` disables wrapping)."""
        self.wrap = wrap
        return self._touch()

    @property
    def lines(self) -> int:
        return len(self._layout())

    def lines_text(self) -> list[str]:
        return ["".join(t for t, _ in line) for line in self._layout()]

    def _layout(self) -> list[list[Run]]:
        lines = split_runs(self._runs, self.wrap)
        if self.align in ("right", "center") and self.wrap:
            out = []
            for ln in lines:
                plain = "".join(t for t, _ in ln)
                gap = self.wrap - text_width(plain)
                if gap > 0:
                    filler = Style()
                    left = gap if self.align == "right" else gap // 2
                    padded: list[Run] = []
                    if left:
                        padded.append((" " * left, filler))
                    padded.extend(ln)
                    if self.align == "center" and gap - left:
                        padded.append((" " * (gap - left), filler))
                    out.append(padded)
                else:
                    out.append(ln)
            return out
        return lines

    def rect(self) -> tuple[int, int, int, int]:
        """``(x, y, width, height)`` — the cells this object covers."""
        layout = self._layout()
        h = len(layout)
        w = max((text_width("".join(t for t, _ in ln)) for ln in layout), default=0)
        return self.x, self.y, w, h

    def contains(self, x: int, y: int) -> bool:
        """True when ``(x, y)`` falls inside this object's rectangle."""
        ox, oy, w, h = self.rect()
        return ox <= x < ox + w and oy <= y < oy + h

    def cell_at(self, x: int, y: int) -> str:
        """The character this object paints at ``(x, y)`` (``""`` if none)."""
        if not self.contains(x, y):
            return ""
        layout = self._layout()
        line = "".join(t for t, _ in layout[y - self.y])
        col = x - self.x
        pos = 0
        for ch in line:
            if pos == col:
                return ch
            pos += char_width(ch)
        return ""

    # -- state -----------------------------------------------------------
    def show(self) -> TextObject:
        self.visible = True
        return self._touch()

    def hide(self) -> TextObject:
        self.visible = False
        return self._touch()

    def remove(self) -> None:
        """Delete this object from its layer (and therefore the screen)."""
        self.layer.remove(self.id)

    def _touch(self) -> TextObject:
        self.layer.mark_dirty()
        return self

    # -- baking ----------------------------------------------------------
    def paint(self, buffer) -> None:  # noqa: ANN001 - Buffer, avoids a cycle
        """Rasterise this object into ``buffer``."""
        if not self.visible:
            return
        for offset, line in enumerate(self._layout()):
            if not line:
                continue
            buffer.put_runs(self.x, self.y + offset, line, clip=self.clip)

    def __repr__(self) -> str:
        shown = self.text if len(self.text) <= 24 else self.text[:23] + "…"
        return f"<TextObject {self.id!r} at ({self.x},{self.y}) {shown!r}>"


_id_counter = itertools.count(1)


def next_id(prefix: str = "obj") -> str:
    return f"{prefix}-{next(_id_counter)}"


__all__ = ["TextObject", "next_id", "split_runs"]

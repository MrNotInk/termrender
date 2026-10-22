"""The single unit of the screen: one character plus one style."""

from __future__ import annotations

from dataclasses import dataclass

from .style import Style
from .text import CONTINUATION, EMPTY

_DEFAULT = Style()


@dataclass(frozen=True, slots=True)
class Cell:
    """One terminal cell.

    ``char`` is a single printable character, :data:`~termrender.text.EMPTY`
    for an untouched cell (nothing is drawn, layers below show through) or
    :data:`~termrender.text.CONTINUATION` for the right half of a
    double-width glyph.
    """

    char: str = " "
    style: Style = _DEFAULT

    @property
    def empty(self) -> bool:
        """True when this cell paints nothing at all (layer transparency)."""
        return self.char == EMPTY

    @property
    def blank(self) -> bool:
        """True when this cell paints nothing *visible*."""
        return self.char in (EMPTY, CONTINUATION) or (self.char == " " and self.style.bg.is_default)

    @property
    def printable(self) -> str:
        """The character to send to the terminal."""
        return " " if self.char in (EMPTY, CONTINUATION) else self.char

    def with_style(self, style: Style) -> Cell:
        return Cell(self.char, style)

    def __str__(self) -> str:
        return self.printable


EMPTY_CELL = Cell(EMPTY, _DEFAULT)
BLANK_CELL = Cell(" ", _DEFAULT)
CONT_CELL = Cell(CONTINUATION, _DEFAULT)

#: Small cache so drawing the same styled character repeatedly is cheap.
_CELL_CACHE: dict[tuple[str, int], Cell] = {}


def cell(char: str, style: Style = _DEFAULT) -> Cell:
    """Interned :class:`Cell` constructor (identity-comparable, fast)."""
    if style is _DEFAULT:
        if char == " ":
            return BLANK_CELL
        if char == EMPTY:
            return EMPTY_CELL
        if char == CONTINUATION:
            return CONT_CELL
    # Keyed by identity, not equality: hashing a Style costs a tuple hash of
    # four Colours, and every cached Cell keeps its Style alive, so the id can
    # never be recycled behind our back. Equal-but-distinct styles simply get
    # separate entries.
    key = (char, id(style))
    hit = _CELL_CACHE.get(key)
    if hit is None:
        hit = Cell(char, style)
        if len(_CELL_CACHE) < 8192:
            _CELL_CACHE[key] = hit
    return hit


__all__ = ["BLANK_CELL", "CONT_CELL", "Cell", "EMPTY_CELL", "cell"]

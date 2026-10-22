"""The cell grid — the canvas every drawing operation writes into."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from . import text as _text
from .cell import EMPTY_CELL, Cell
from .cell import cell as make_cell
from .chars import BoxChars, box_set
from .style import Style, normalize_style

_TAB = 4


def _is_blank(ch: str) -> bool:
    """True for characters that carry no ink (space and the sentinels)."""
    return ch in ("", " ", _text.EMPTY, _text.CONTINUATION)


#: Keywords that only mean something to a Screen. A Buffer accepts and drops
#: them so widgets can draw into either target unchanged.
_NOT_STYLE = ("id", "layer", "z", "visible")


class Buffer:
    """A width × height grid of :class:`~termrender.cell.Cell`.

    This is a plain off-screen bitmap: nothing here talks to the terminal.
    Draw into it, inspect it, blit buffers into each other, then hand it to a
    :class:`~termrender.screen.Screen` (or call :meth:`ansi`) to display it.

    Coordinates are 0-based with the origin at the **top-left**:
    ``x`` is the column, ``y`` is the row.
    """

    __slots__ = ("_cells", "_width", "_height", "default_style", "tab_size")

    def __init__(
        self,
        width: int,
        height: int,
        *,
        default_style: Style | None = None,
        fill: str = "",
        tab_size: int = _TAB,
    ) -> None:
        if width < 0 or height < 0:
            raise ValueError(f"buffer size must be non-negative, got {width}x{height}")
        self._width = int(width)
        self._height = int(height)
        self.default_style = normalize_style(default_style)
        self.tab_size = tab_size
        base = EMPTY_CELL if fill in ("", None) else make_cell(fill, self.default_style)
        self._cells: list[Cell] = [base] * (self._width * self._height)

    # -- geometry --------------------------------------------------------
    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def size(self) -> tuple[int, int]:
        return self._width, self._height

    @property
    def area(self) -> int:
        return self._width * self._height

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self._width and 0 <= y < self._height

    def index(self, x: int, y: int) -> int:
        """Flat index of ``(x, y)``; wraps negative coordinates Python-style."""
        if x < 0:
            x += self._width
        if y < 0:
            y += self._height
        return y * self._width + x

    def clamp_rect(self, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
        """Clip a rectangle to the buffer, returning ``(x, y, w, h)``."""
        x2 = min(self._width, x + w)
        y2 = min(self._height, y + h)
        x1 = max(0, x)
        y1 = max(0, y)
        return x1, y1, max(0, x2 - x1), max(0, y2 - y1)

    # -- single cells ----------------------------------------------------
    def get(self, x: int, y: int) -> Cell:
        """The :class:`Cell` at ``(x, y)`` (out of bounds → empty cell)."""
        if not self.in_bounds(x, y):
            return EMPTY_CELL
        return self._cells[y * self._width + x]

    def set(self, x: int, y: int, value: Cell) -> bool:
        """Write one cell. Returns False when the cell was out of bounds."""
        if not self.in_bounds(x, y):
            return False
        self._cells[y * self._width + x] = value
        return True

    def char(self, x: int, y: int) -> str:
        return self.get(x, y).char

    def style_at(self, x: int, y: int) -> Style:
        return self.get(x, y).style

    # -- text ------------------------------------------------------------
    def put(
        self,
        x: int,
        y: int,
        content: str,
        style: Style | dict | None = None,
        /,
        *,
        wrap: bool = False,
        clip: bool = True,
        base: Style | None = None,
        **kwargs: object,
    ) -> int:
        """Write ``content`` at ``(x, y)``. Returns the number of cells used.

        ``content`` may contain newlines (each starts a new row) and tabs.
        Double-width characters occupy two cells and their right half is
        filled with a continuation marker, so erasing and overwriting stay
        aligned.

        ``clip=True`` (default) drops glyphs that do not fit on the line;
        ``clip=False`` lets them spill past the right edge (still bounded by
        the buffer). ``wrap=True`` moves to the next line instead.
        """
        for ignored in _NOT_STYLE:
            kwargs.pop(ignored, None)
        st = normalize_style(style, base=base, **kwargs)
        if not content or self._width == 0 or self._height == 0:
            return 0

        cells = self._cells
        width = self._width
        height = self._height
        written = 0
        col, row = x, y

        for ch in content:
            if ch == "\n":
                col = x
                row += 1
                if row >= height:
                    break
                continue
            if ch == "\r":
                col = x
                continue
            if ch == "\t":
                col = ((col - x) // self.tab_size + 1) * self.tab_size + x
                continue

            w = _text.char_width(ch)
            if w == 0:
                # combining mark: fold into the previous cell if possible
                if col > 0 and 0 <= row < height:
                    prev = cells[row * width + col - 1]
                    if prev.char not in _text.BLANK:
                        cells[row * width + col - 1] = make_cell(prev.char + ch, st)
                        continue
                continue

            if col + w > width:
                if wrap and row + 1 < height:
                    col = x
                    row += 1
                elif clip or col >= width:
                    break

            if not (0 <= row < height):
                break

            self._stamp(col, row, ch, w, st)
            written += w
            col += w

        return written

    def _stamp(self, x: int, y: int, ch: str, w: int, style: Style) -> None:
        """Write a glyph of width ``w`` at ``(x, y)``, fixing neighbours."""
        cells = self._cells
        width = self._width
        row = y * width
        i = row + x

        existing = cells[i]
        if existing.char == _text.CONTINUATION and x > 0:
            cells[i - 1] = EMPTY_CELL  # orphaned left half
        if w == 1 and _text.char_width(existing.char) == 2 and x + 1 < width:
            cells[i + 1] = EMPTY_CELL  # orphaned right half

        cells[i] = make_cell(ch, style)

        if w == 2 and x + 1 < width:
            right = cells[i + 1]
            if _text.char_width(right.char) == 2 and x + 2 < width:
                cells[i + 2] = EMPTY_CELL
            cells[i + 1] = make_cell(_text.CONTINUATION, style)

    def put_runs(
        self,
        x: int,
        y: int,
        runs: Sequence[tuple[str, Style | dict | None]],
        *,
        wrap: bool = False,
        clip: bool = True,
        base: Style | None = None,
        **kwargs: object,
    ) -> int:
        """Write ``[(text, style), ...]`` as one continuous line.

        Screen-only keywords (``id``, ``layer``) are accepted and ignored, so the
        widgets in :mod:`termrender.widgets` can draw into a Buffer or a Screen
        without branching on the target.
        """
        used = 0
        for chunk, style in runs:
            st = normalize_style(style, base=base)
            used += self.put(x + used, y, chunk, st, wrap=wrap, clip=clip)
        return used

    def erase(self, x: int, y: int, w: int = 1, h: int = 1) -> int:
        """Remove whatever is in the rectangle, making it transparent.

        Erased cells paint *nothing*: layers below show through and, on the
        bottom-most buffer, they render as the screen background.
        """
        x, y, w, h = self.clamp_rect(x, y, w, h)
        if not w or not h:
            return 0
        cells = self._cells
        width = self._width
        count = 0
        for row in range(y, y + h):
            base = row * width
            # never leave a wide glyph cut in half
            if x > 0 and cells[base + x].char == _text.CONTINUATION:
                cells[base + x - 1] = EMPTY_CELL
                count += 1
            for col in range(x, x + w):
                i = base + col
                old = cells[i]
                if old is not EMPTY_CELL:
                    if _text.char_width(old.char) == 2 and col + 1 < x + w:
                        cells[i + 1] = EMPTY_CELL
                    cells[i] = EMPTY_CELL
                    count += 1
            if x + w < width and cells[base + x + w].char == _text.CONTINUATION:
                cells[base + x + w] = EMPTY_CELL
        return count

    def blank(self, x: int, y: int, w: int = 1, h: int = 1, style: Style | None = None) -> int:
        """Fill a rectangle with spaces (opaque, unlike :meth:`erase`)."""
        return self.fill(x, y, w, h, " ", style or self.default_style)

    def fill(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        char: str = " ",
        style: Style | dict | None = None,
        /,
        **kwargs: object,
    ) -> int:
        """Fill a rectangle with ``char``. Returns the number of cells set."""
        st = normalize_style(style, base=self.default_style, **kwargs)
        x, y, w, h = self.clamp_rect(x, y, w, h)
        if not w or not h:
            return 0
        the_cell = make_cell(char or " ", st)
        cells = self._cells
        width = self._width
        line = [the_cell] * w
        for row in range(y, y + h):
            start = row * width + x
            cells[start:start + w] = line
        return w * h

    def clear(self, style: Style | dict | None = None, char: str = "") -> None:
        """Reset the whole buffer to transparent (``char=""``) or spaces."""
        st = normalize_style(style, base=self.default_style)
        base = EMPTY_CELL if char == "" else make_cell(char, st)
        self._cells = [base] * (self._width * self._height)

    # -- shapes ----------------------------------------------------------
    def rect(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        style: Style | dict | None = None,
        *,
        chars: str | BoxChars | None = None,
        fill: str | None = None,
        fill_style: Style | dict | None = None,
        title: str = "",
        title_align: str = "left",
        title_style: Style | dict | None = None,
        **kwargs: object,
    ) -> None:
        """Draw a box outline, optionally filled and titled."""
        st = normalize_style(style, base=self.default_style, **kwargs)
        box = box_set(chars)
        if w < 2 or h < 2:
            if w > 0 and h > 0:
                self.fill(x, y, w, h, fill or " ", fill_style or st)
            return
        if fill is not None:
            self.fill(x + 1, y + 1, w - 2, h - 2, fill, fill_style or st)
        self.hline(x + 1, y, w - 2, box.horizontal, st)
        self.hline(x + 1, y + h - 1, w - 2, box.horizontal, st)
        self.vline(x, y + 1, h - 2, box.vertical, st)
        self.vline(x + w - 1, y + 1, h - 2, box.vertical, st)
        self.put(x, y, box.top_left, st)
        self.put(x + w - 1, y, box.top_right, st)
        self.put(x, y + h - 1, box.bottom_left, st)
        self.put(x + w - 1, y + h - 1, box.bottom_right, st)
        if title:
            inner = w - 2
            text = _text.truncate(f" {title} ", inner)
            label = _text.pad(text, inner, title_align, fill=box.horizontal)
            self.put(x + 1, y, label, normalize_style(title_style, base=st))

    def hline(self, x: int, y: int, length: int, char: str = "─", style: Style | None = None,
              **kwargs: object) -> int:
        st = style or self.default_style
        return self.fill(x, y, length, 1, char, st)

    def vline(self, x: int, y: int, length: int, char: str = "│", style: Style | None = None,
              **kwargs: object) -> int:
        st = style or self.default_style
        return self.fill(x, y, 1, length, char, st)

    def line(self, x0: int, y0: int, x1: int, y1: int, char: str = "•",
             style: Style | None = None, **kwargs: object) -> int:
        """Bresenham line from ``(x0, y0)`` to ``(x1, y1)``."""
        st = style or self.default_style
        the_cell = make_cell(char, st)
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        drawn = 0
        while True:
            if 0 <= x0 < self._width and 0 <= y0 < self._height:
                self._cells[y0 * self._width + x0] = the_cell
                drawn += 1
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy
        return drawn

    # -- compositing -----------------------------------------------------
    def blit(
        self,
        other: Buffer,
        x: int = 0,
        y: int = 0,
        *,
        transparent: bool = True,
        src_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        """Copy ``other`` onto this buffer at ``(x, y)``.

        With ``transparent=True`` only non-empty cells are copied, so layers
        stack. With ``False`` the source rectangle overwrites everything.
        """
        sx, sy, sw, sh = src_rect if src_rect else (0, 0, other.width, other.height)
        sw = min(sw, other.width - sx)
        sh = min(sh, other.height - sy)
        if sw <= 0 or sh <= 0:
            return

        src = other._cells
        dst = self._cells

        # fast path: aligned, opaque or full-width rows -> slice assignment
        if x == 0 and sw == self._width and not transparent:
            for row in range(sh):
                oy = y + row
                if not 0 <= oy < self._height:
                    continue
                start = (sy + row) * other.width + sx
                dst[oy * self._width:(oy + 1) * self._width] = src[start:start + sw]
            return

        for row in range(sh):
            oy = y + row
            if not 0 <= oy < self._height:
                continue
            src_start = (sy + row) * other.width + sx
            chunk = src[src_start:src_start + sw]
            dst_start = oy * self._width + x

            if x >= 0 and x + sw <= self._width:
                stop = dst_start + sw
                if transparent:
                    dst[dst_start:stop] = [
                        d if sc.empty else sc
                        for d, sc in zip(dst[dst_start:stop], chunk, strict=False)
                    ]
                else:
                    dst[dst_start:stop] = chunk
                continue

            for col, sc in enumerate(chunk):
                ox = x + col
                if not 0 <= ox < self._width:
                    continue
                if transparent and sc.empty:
                    continue
                dst[dst_start + col] = sc

    def scroll(self, lines: int = 1, columns: int = 0) -> None:
        """Shift content by ``lines`` rows / ``columns`` cells.

        Positive ``lines`` moves content down (like a log view scrolling up).
        Vacated cells become transparent.
        """
        if (lines == 0 and columns == 0) or not self._width or not self._height:
            return
        fresh = Buffer(self._width, self._height, default_style=self.default_style)
        fresh.blit(self, columns, lines, transparent=False)
        self._cells = fresh._cells

    def copy(self) -> Buffer:
        clone = Buffer.__new__(Buffer)
        clone._width = self._width
        clone._height = self._height
        clone.default_style = self.default_style
        clone.tab_size = self.tab_size
        clone._cells = self._cells[:]
        return clone

    __copy__ = copy

    def resize(self, width: int, height: int, *, keep: bool = True, anchor: str = "topleft") -> None:
        """Resize the grid, keeping existing content anchored per ``anchor``.

        ``anchor`` accepts any combination of ``top|bottom|center`` and
        ``left|right|center`` (e.g. ``"center"``, ``"bottomleft"``).
        """
        width, height = max(0, int(width)), max(0, int(height))
        if (width, height) == (self._width, self._height):
            return
        old = self
        fresh = Buffer(width, height, default_style=self.default_style, tab_size=self.tab_size)
        if keep and old.width and old.height:
            dx = dy = 0
            if "right" in anchor:
                dx = width - old.width
            elif "center" in anchor and "left" not in anchor and "right" not in anchor:
                dx = (width - old.width) // 2
            if "bottom" in anchor:
                dy = height - old.height
            elif anchor in ("center",) or ("center" in anchor and "top" not in anchor and "bottom" not in anchor):
                dy = (height - old.height) // 2
            fresh.blit(old, dx, dy, transparent=False)
        self._width, self._height = width, height
        self._cells = fresh._cells

    # -- reading ---------------------------------------------------------
    def row_text(self, y: int, *, rstrip: bool = True) -> str:
        """Row ``y`` as a plain string (continuation cells become spaces)."""
        if not 0 <= y < self._height:
            return ""
        start = y * self._width
        out = [c.printable for c in self._cells[start:start + self._width] if c.char != _text.CONTINUATION]
        line = "".join(out)
        return line.rstrip() if rstrip else line

    def rows(self, *, rstrip: bool = True) -> list[str]:
        return [self.row_text(y, rstrip=rstrip) for y in range(self._height)]

    def text(self, *, rstrip: bool = True, trailing: bool = False) -> str:
        """Whole buffer as plain text — the frame without any styling."""
        lines = self.rows(rstrip=rstrip)
        if not trailing:
            while lines and not lines[-1]:
                lines.pop()
        return "\n".join(lines)

    def ansi(self, *, rstrip: bool = True, depth: object = None) -> str:
        """Whole buffer as an ANSI-coloured string (one line per row)."""
        from .style import ColorDepth, style_runs
        from .style import Style as _Style

        d = depth if depth is not None else ColorDepth.TRUECOLOR
        blank = _Style()          # one instance, reused: empty cells are common
        lines: list[str] = []
        for y in range(self._height):
            start = y * self._width
            row = self._cells[start:start + self._width]
            if rstrip:
                # Trailing blanks have to go *before* the escapes are built:
                # once a run is coloured, rstripping the string can no longer
                # see the spaces it ends with.
                end = len(row)
                while end and row[end - 1].blank:
                    end -= 1
                row = row[:end]
            runs: list[tuple[str, _Style]] = []
            for c in row:
                # An untouched cell paints nothing, but it still occupies a
                # column, so it has to come out as a plain space — otherwise
                # ``ansi()`` and ``row_text()`` disagree about where text sits.
                char, style = (" ", blank) if c.empty else (c.printable, c.style)
                if runs and runs[-1][1] == style:
                    runs[-1] = (runs[-1][0] + char, style)
                else:
                    runs.append((char, style))
            line = style_runs(runs, d)
            if rstrip:
                line = line.rstrip()
            lines.append(line)
        return "\n".join(lines)

    def bbox(self) -> tuple[int, int, int, int] | None:
        """``(x, y, w, h)`` of the non-blank content, or None when empty."""
        min_x = min_y = 1 << 30
        max_x = max_y = -1
        for y in range(self._height):
            start = y * self._width
            for x in range(self._width):
                c = self._cells[start + x]
                if c.empty or c.char in (" ", _text.CONTINUATION):
                    continue
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
        if max_x < 0:
            return None
        return min_x, min_y, max_x - min_x + 1, max_y - min_y + 1

    def run_bounds(self, x: int, y: int) -> tuple[int, int] | None:
        """``(start_x, end_x)`` of the non-blank run containing ``(x, y)``.

        Returns None when the cell itself is blank. This is what makes
        "remove the text at (x, y)" delete the whole word rather than one
        character.
        """
        if not self.in_bounds(x, y) or _is_blank(self.get(x, y).char):
            return None
        start = x
        while start > 0:
            previous = self._cells[y * self._width + start - 1].char
            if previous == _text.CONTINUATION:
                start -= 2
                continue
            if _is_blank(previous):
                break
            start -= 1
        stop = x
        while stop + 1 < self._width:
            following = self._cells[y * self._width + stop + 1].char
            if following == _text.CONTINUATION or not _is_blank(following):
                stop += 1
            else:
                break
        return max(0, start), stop

    def find(self, needle: str) -> list[tuple[int, int]]:
        """Every ``(x, y)`` where ``needle`` appears (plain text search)."""
        if not needle:
            return []
        hits: list[tuple[int, int]] = []
        first = needle[0]
        for y in range(self._height):
            start = y * self._width
            for x in range(self._width):
                if self._cells[start + x].char != first:
                    continue
                if self._match(x, y, needle):
                    hits.append((x, y))
        return hits

    def _match(self, x: int, y: int, needle: str) -> bool:
        cells = self._cells
        pos = x
        for ch in needle:
            if pos >= self._width:
                return False
            if cells[y * self._width + pos].char != ch:
                return False
            pos += _text.char_width(ch) or 1
        return True

    def __iter__(self) -> Iterator[list[Cell]]:
        for y in range(self._height):
            start = y * self._width
            yield self._cells[start:start + self._width]

    def __getitem__(self, key: object) -> object:
        if isinstance(key, tuple) and len(key) == 2:
            return self.get(int(key[0]), int(key[1]))
        if isinstance(key, int):
            start = key * self._width
            return self._cells[start:start + self._width]
        raise TypeError("index with (x, y) or a row number")

    def __setitem__(self, key: object, value: object) -> None:
        if isinstance(key, tuple) and len(key) == 2 and isinstance(value, Cell):
            self.set(int(key[0]), int(key[1]), value)
            return
        if isinstance(key, tuple) and len(key) == 2 and isinstance(value, str):
            self.put(int(key[0]), int(key[1]), value)
            return
        raise TypeError("assign buf[x, y] = Cell(...) or buf[x, y] = 'text'")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Buffer):
            return NotImplemented
        return self._width == other._width and self._height == other._height and self._cells == other._cells

    def __repr__(self) -> str:
        return f"<Buffer {self._width}x{self._height}>"


def diff(a: Buffer, b: Buffer) -> list[tuple[int, int, Cell]]:
    """Cells that differ between two equally sized buffers."""
    if a.size != b.size:
        raise ValueError("buffers must be the same size to diff")
    ac, bc = a._cells, b._cells
    w = a.width
    out: list[tuple[int, int, Cell]] = []
    for i, (left, right) in enumerate(zip(ac, bc, strict=True)):
        if left is not right and left != right:
            out.append((i % w, i // w, right))
    return out


def stack(buffers: Iterable[Buffer], width: int, height: int) -> Buffer:
    """Composite buffers bottom-to-top into a new buffer."""
    out = Buffer(width, height)
    for buf in buffers:
        out.blit(buf, transparent=True)
    return out


__all__ = ["Buffer", "diff", "stack"]

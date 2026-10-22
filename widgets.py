"""Ready-made pieces built out of the same put / fill / rect primitives.

Every widget accepts either a :class:`~termrender.screen.Screen` or a
:class:`~termrender.layer.Layer` as its first argument — both expose the same
drawing surface — and returns whatever handle is useful for updating it later.
"""

from __future__ import annotations

from collections.abc import Sequence

from .chars import BLOCKS, SHADES, V_BLOCKS, BoxChars, box_set
from .objects import TextObject
from .style import Color, Style, normalize_style
from .text import pad, text_width, truncate, wrap

Target = object  # Screen or Layer


def panel(
    target: Target,
    x: int,
    y: int,
    w: int,
    h: int,
    title: str = "",
    style: Style | dict | None = None,
    *,
    chars: str | BoxChars | None = None,
    title_align: str = "left",
    fill: str | None = None,
    fill_style: Style | dict | None = None,
    layer: str | None = None,
    **kwargs: object,
) -> tuple[int, int, int, int]:
    """Draw a bordered panel and return its *inner* rectangle ``(x, y, w, h)``."""
    target.rect(  # type: ignore[attr-defined]
        x, y, w, h, normalize_style(style, **kwargs),
        chars=chars, title=title, title_align=title_align,
        fill=fill, fill_style=normalize_style(fill_style, **kwargs) if fill_style else None,
        **_layer_kw(layer),
    )
    return x + 1, y + 1, max(0, w - 2), max(0, h - 2)


def _layer_kw(layer: str | None) -> dict[str, object]:
    """``layer=`` only when there is one.

    Screen accepts a layer on every draw call; Buffer does not have the concept.
    Passing ``layer=None`` through would break Buffer targets, so it is omitted.
    """
    return {"layer": layer} if layer is not None else {}


def bar(
    target: Target,
    x: int,
    y: int,
    width: int,
    value: float,
    *,
    maximum: float = 1.0,
    char: str = "█",
    partial: bool = True,
    style: Style | dict | None = None,
    empty_char: str = " ",
    empty_style: Style | dict | None = None,
    gradient: Sequence[Color | str] | None = None,
    label: str = "",
    label_align: str = "center",
    id: str | None = None,  # noqa: A002
    layer: str | None = None,
    **kwargs: object,
) -> float:
    """A progress / meter bar. Returns the fraction drawn (0..1).

    ``partial=True`` uses eighth-block glyphs so the fill edge is sub-cell
    accurate; ``gradient`` colours each cell along a ramp (great for CPU or
    health meters); ``label`` is overlaid on top (``"%"`` in it is replaced
    by the percentage).
    """
    width = max(0, int(width))
    if width == 0:
        return 0.0
    fraction = 0.0 if maximum == 0 else max(0.0, min(1.0, value / maximum))
    st = normalize_style(style, **kwargs)
    empty = normalize_style(empty_style, **kwargs) if empty_style is not None else st.with_(bg=Color.parse("#303030"))
    ramp = BLOCKS if char in ("█", " ") else char * 8

    filled = fraction * width
    whole = int(filled)
    if whole >= width:
        whole, frac_index = width, 0
    else:
        frac_index = int(round((filled - whole) * 8))
        if frac_index >= 8:
            whole, frac_index = whole + 1, 0

    key = id or f"bar@{x},{y}"
    colours = [Color.parse(c) for c in gradient] if gradient else None
    runs: list[tuple[str, Style]] = []
    for i in range(width):
        if i < whole:
            glyph, cell_style = ramp[-1], st
        elif i == whole and frac_index:
            glyph, cell_style = ramp[frac_index], st
        else:
            glyph, cell_style = empty_char, empty
        if colours and glyph != empty_char:
            cell_style = cell_style.with_(fg=_sample(colours, i / max(1, width - 1)))
        runs.append((glyph, cell_style))
    target.put_runs(x, y, runs, id=key, **_layer_kw(layer))  # type: ignore[attr-defined]

    if label:
        text = truncate(label.replace("%", f"{fraction * 100:.0f}%"), width, "")
        offset = {"right": width - text_width(text), "center": (width - text_width(text)) // 2}.get(
            label_align, 0)
        target.put(x + offset, y, text, st.with_(bold=True), id=f"{key}-label",
                   **_layer_kw(layer))  # type: ignore[attr-defined]
    return fraction


def _sample(colours: Sequence[Color], t: float) -> Color:
    if len(colours) == 1:
        return colours[0]
    scaled = t * (len(colours) - 1)
    low = int(scaled)
    high = min(len(colours) - 1, low + 1)
    return Color.lerp(colours[low], colours[high], scaled - low)


def sparkline(
    target: Target,
    x: int,
    y: int,
    values: Sequence[float],
    width: int | None = None,
    *,
    style: Style | dict | None = None,
    glyphs: Sequence[str] = V_BLOCKS,
    minimum: float | None = None,
    maximum: float | None = None,
    layer: str | None = None,
    **kwargs: object,
) -> int:
    """A compact ``▁▂▃▅▇`` chart of ``values``. Returns cells written."""
    st = normalize_style(style, **kwargs)
    if width is None:
        width = len(values)
    if not values or width <= 0:
        return 0
    step = len(values) / width
    low = min(values) if minimum is None else minimum
    high = max(values) if maximum is None else maximum
    span = (high - low) or 1.0
    out: list[str] = []
    for i in range(width):
        sample = values[min(len(values) - 1, int(i * step))]
        level = int(round((sample - low) / span * (len(glyphs) - 1)))
        out.append(glyphs[max(0, min(len(glyphs) - 1, level))])
    target.put(x, y, "".join(out), st, **_layer_kw(layer))  # type: ignore[attr-defined]
    return width


def heatmap(
    target: Target,
    x: int,
    y: int,
    grid: Sequence[Sequence[float]],
    *,
    style: Style | dict | None = None,
    glyphs: Sequence[str] = SHADES,
    palette: Sequence[Color | str] | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    layer: str | None = None,
) -> tuple[int, int]:
    """Draw a 2-D grid of values as shaded (and optionally coloured) cells."""
    st = normalize_style(style)
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    flat = [v for row in grid for v in row]
    if not flat:
        return 0, 0
    low = min(flat) if minimum is None else minimum
    high = max(flat) if maximum is None else maximum
    span = (high - low) or 1.0
    colours = [Color.parse(c) for c in palette] if palette else None
    for ry, row in enumerate(grid):
        for rx, value in enumerate(row):
            t = (value - low) / span
            glyph = glyphs[max(0, min(len(glyphs) - 1, int(t * (len(glyphs) - 1) + 0.5)))]
            cell_style = st
            if colours:
                cell_style = st.with_(fg=_sample(colours, t))
            target.put(x + rx, y + ry, glyph, cell_style,
                       **_layer_kw(layer))  # type: ignore[attr-defined]
    return cols, rows


def table(
    target: Target,
    x: int,
    y: int,
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    align: Sequence[str] | str = "left",
    padding: int = 1,
    style: Style | dict | None = None,
    header_style: Style | dict | None = None,
    separator: str = " ",
    border: bool = False,
    chars: str | BoxChars | None = None,
    widths: Sequence[int] | None = None,
    max_width: int | None = None,
    layer: str | None = None,
) -> tuple[int, int]:
    """A column-aligned text table. Returns ``(width, height)`` drawn."""
    st = normalize_style(style)
    head_st = normalize_style(header_style, base=st, bold=True)
    box = box_set(chars)
    columns = list(zip(*([headers] + [[str(c) for c in row] for row in rows]), strict=False)) if (headers or rows) else []
    if not columns:
        return 0, 0

    if widths:
        col_widths = list(widths)
    else:
        col_widths = [max(text_width(str(cell)) for cell in column) for column in columns]

    total = sum(col_widths) + padding * 2 * len(col_widths) + len(separator) * (len(col_widths) - 1)
    if max_width and total > max_width:
        excess = total - max_width
        for i in range(len(col_widths) - 1, -1, -1):
            if excess <= 0:
                break
            cut = min(excess, col_widths[i] - 1)
            if cut > 0:
                col_widths[i] -= cut
                excess -= cut

    aligns = [align] * len(col_widths) if isinstance(align, str) else list(align)
    oy = y
    if border:
        target.rect(x, y, total + 2, len(rows) + (1 if headers else 0) + 2, st, chars=box,
                    **_layer_kw(layer))  # type: ignore[attr-defined]
        oy = y + 1
        x += 1

    def draw_row(values: Sequence[str], row_style: Style) -> None:
        nonlocal oy
        cx = x
        for i, value in enumerate(values):
            w = col_widths[i] if i < len(col_widths) else 0
            cell_align = aligns[i] if i < len(aligns) else "left"
            text = pad(truncate(str(value), w, ""), w, cell_align)
            target.put(cx + padding, oy, text, row_style, **_layer_kw(layer))  # type: ignore[attr-defined]
            cx += w + padding * 2 + text_width(separator)
            if i < len(values) - 1 and separator.strip():
                target.put(cx - text_width(separator), oy, separator, row_style,
                           **_layer_kw(layer))  # type: ignore[attr-defined]
        oy += 1

    if headers:
        draw_row([str(h) for h in headers], head_st)
        rule = box.horizontal if border else "─"
        target.hline(x, oy, total, rule, st if border else st.with_(dim=True),
                     **_layer_kw(layer))  # type: ignore[attr-defined]
        oy += 1

    for row in rows:
        draw_row([str(c) for c in row], st)

    return total + (2 if border else 0), oy - y


def menu(
    target: Target,
    x: int,
    y: int,
    items: Sequence[str],
    selected: int = 0,
    *,
    style: Style | dict | None = None,
    selected_style: Style | dict | None = None,
    marker: str = "❯ ",
    blank_marker: str | None = None,
    id_prefix: str = "menu",
    layer: str | None = None,
) -> int:
    """A vertical selectable list. Returns the number of rows drawn."""
    st = normalize_style(style)
    sel_st = normalize_style(selected_style, base=st, bold=True, fg=Color.parse("cyan"))
    blank = (" " * text_width(marker)) if blank_marker is None else blank_marker
    for i, item in enumerate(items):
        active = i == selected
        target.put(  # type: ignore[attr-defined]
            x, y + i,
            (marker if active else blank) + item,
            sel_st if active else st,
            id=f"{id_prefix}-{i}",
            **_layer_kw(layer),
        )
    return len(items)


def text_block(
    target: Target,
    x: int,
    y: int,
    content: str,
    width: int,
    style: Style | dict | None = None,
    *,
    align: str = "left",
    id: str | None = None,  # noqa: A002
    layer: str | None = None,
    **kwargs: object,
) -> TextObject:
    """Word-wrapped paragraph as a single removable object."""
    st = normalize_style(style, **kwargs)
    return target.put(x, y, content, st, wrap=width, align=align, id=id,
                      **_layer_kw(layer))  # type: ignore[attr-defined,return-value]


def key_hints(
    target: Target,
    x: int,
    y: int,
    hints: Sequence[tuple[str, str]],
    *,
    style: Style | dict | None = None,
    key_style: Style | dict | None = None,
    separator: str = "  ",
    id: str = "hints",
    layer: str | None = None,
) -> TextObject:
    """A ``q quit  ↑↓ move  ⏎ select`` status line as one styled object."""
    st = normalize_style(style, dim=True) if style is not None else Style(dim=True)
    key_st = normalize_style(key_style) if key_style is not None else st.with_(bold=True, dim=False)
    runs: list[tuple[str, Style]] = []
    for i, (key, description) in enumerate(hints):
        if i:
            runs.append((separator, st))
        runs.append((key, key_st))
        runs.append((f" {description}", st))
    return target.put_runs(x, y, runs, id=id)  # type: ignore[attr-defined,return-value]


def clock_face(
    target: Target,
    x: int,
    y: int,
    text: str,
    *,
    style: Style | dict | None = None,
    id: str = "clock",
    layer: str | None = None,
) -> TextObject:
    """Big-ish centred label, handy for timers and counters."""
    st = normalize_style(style, bold=True)
    return target.put(x, y, text, st, id=id, **_layer_kw(layer))  # type: ignore[attr-defined,return-value]


def gradient_text(
    target: Target,
    x: int,
    y: int,
    content: str,
    palette: Sequence[Color | str],
    *,
    style: Style | dict | None = None,
    id: str | None = None,
    layer: str | None = None,
) -> TextObject:
    """Text with a horizontal colour gradient across its characters."""
    st = normalize_style(style)
    colours = [Color.parse(c) for c in palette]
    chars = [c for c in content if c != "\n"]
    runs = []
    for i, ch in enumerate(chars):
        runs.append((ch, st.with_(fg=_sample(colours, i / max(1, len(chars) - 1)))))
    return target.put_runs(x, y, runs, id=id)  # type: ignore[attr-defined,return-value]


def wrapped_lines(content: str, width: int) -> list[str]:
    """Expose the wrapper the widgets use (handy for your own layouts)."""
    return wrap(content, width)


__all__ = [
    "bar",
    "clock_face",
    "gradient_text",
    "heatmap",
    "key_hints",
    "menu",
    "panel",
    "sparkline",
    "table",
    "text_block",
    "wrapped_lines",
]

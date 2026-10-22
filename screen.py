"""The rendering engine: composites layers and paints the delta to a terminal."""

from __future__ import annotations

import atexit
import contextlib
import signal
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

from . import ansi
from .buffer import Buffer
from .cell import Cell
from .chars import BoxChars, box_set
from .config import Config, Stats, is_tty, terminal_size
from .input import Event, Keyboard
from .layer import Layer
from .objects import TextObject
from .style import ColorDepth, Style, normalize_style
from .text import CONTINUATION, EMPTY

_DEFAULT = Style()


@dataclass(frozen=True, slots=True)
class Match:
    """A located piece of text: what it is, where it is, how to remove it."""

    text: str
    x: int
    y: int
    layer: str | None = None
    object: TextObject | None = None

    @property
    def pos(self) -> tuple[int, int]:
        return self.x, self.y

    @property
    def removable(self) -> bool:
        return self.object is not None

    def remove(self) -> bool:
        """Remove this text if it is an object (raw cells need ``erase``)."""
        if self.object is None:
            return False
        self.object.remove()
        return True

    def __repr__(self) -> str:
        kind = self.object.id if self.object is not None else "raw"
        return f"<Match {self.text!r} at ({self.x},{self.y}) via {kind}>"


def _trimmable(cell: Cell) -> bool:
    """True when the cell paints nothing a terminal would miss.

    A space with a background colour, an underline or any other attribute is
    *not* trimmable: clearing to end-of-line would drop it.
    """
    return cell.char in (" ", EMPTY) and not cell.style


class Screen:
    """A terminal turned into an addressable, compositing render target.

    ``Screen`` owns a stack of :class:`~termrender.layer.Layer` objects and a
    pair of cell grids (front = what is on the terminal, back = what should
    be). :meth:`draw` composites the layers into the back buffer, diffs it
    against the front buffer and emits only the bytes needed to reconcile
    them — so a 200×60 screen updating one character costs one character.

    Basic use::

        from termrender import Screen

        with Screen() as screen:
            label = screen.put(4, 2, "hello", fg="cyan", bold=True)
            screen.draw()
            screen.remove((4, 2))   # locate by coordinate and remove
            screen.draw()

    Every drawing call accepts either ``style=Style(...)`` or loose keyword
    arguments (``fg=``, ``bg=``, ``bold=``, ``underline=``, …), and every
    coordinate is 0-based from the top-left.
    """

    def __init__(self, config: Config | None = None, /, **overrides: object) -> None:
        cfg = config if config is not None else Config()
        if overrides:
            cfg = cfg.replace(**overrides)
        self.config: Config = cfg

        self._width, self._height = cfg.resolved_size()
        self._layers: list[Layer] = []
        self._dirty = True
        self._first = True
        self._composited = False
        self._started = False
        self._cursor_state: bool | None = None
        self._cursor_pos: tuple[int, int] | None = None
        self._term_style: Style | None = None
        self._run_style: Style | None = None
        self._run_cells: int = 0
        self._depth: ColorDepth = cfg.resolved_depth()
        self._stream: object = None
        self._interactive = False
        self._sigwinch_prev: object = None
        self._events: list[Event] = []
        self._pending_resize: tuple[int, int] | None = None

        self.stats = Stats()
        self.keyboard = Keyboard(timeout=cfg.input_timeout)

        self._back = Buffer(self._width, self._height)
        self._front = Buffer(self._width, self._height)

        #: The bottom layer. ``screen.put`` and friends draw here.
        self.base: Layer = self.layer("base", z=0)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
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
    def center(self) -> tuple[int, int]:
        return self._width // 2, self._height // 2

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        """``(x, y, width, height)`` of the whole screen."""
        return 0, 0, self._width, self._height

    @property
    def box_chars(self) -> BoxChars:
        """Box glyphs matching ``config.unicode``."""
        return box_set("rounded" if self.config.unicode else "ascii")

    def invalidate(self) -> None:
        """Force a recomposite (and, if needed, a repaint) on the next draw."""
        self._dirty = True

    def force_repaint(self) -> None:
        """Forget what the terminal shows and repaint everything next draw."""
        self._first = True
        self._dirty = True
        self._term_style = None
        self._cursor_pos = None

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------
    @property
    def layers(self) -> list[Layer]:
        """All layers, bottom to top."""
        return list(self._layers)

    def layer(
        self,
        name: str | None = None,
        *,
        z: int | None = None,
        visible: bool = True,
        transparent: bool = True,
        offset: tuple[int, int] = (0, 0),
        clip: bool = True,
        width: int | None = None,
        height: int | None = None,
    ) -> Layer:
        """Create (or fetch) a layer. Layers with a higher ``z`` draw on top."""
        name = name or f"layer-{len(self._layers)}"
        existing = self.get_layer(name)
        if existing is not None:
            return existing
        layer = Layer(
            name,
            self._width if width is None else width,
            self._height if height is None else height,
            z=len(self._layers) if z is None else z,
            visible=visible,
            transparent=transparent,
            offset=offset,
            clip=clip,
            default_style=self.config.default_style,
            notify=self._on_layer_dirty,
        )
        self._layers.append(layer)
        self._layers.sort(key=lambda item: item.z)
        self._dirty = True
        return layer

    def get_layer(self, name: str) -> Layer | None:
        for layer in self._layers:
            if layer.name == name:
                return layer
        return None

    def remove_layer(self, target: str | Layer) -> bool:
        layer = target if isinstance(target, Layer) else self.get_layer(str(target))
        if layer is None or layer not in self._layers:
            return False
        self._layers.remove(layer)
        self._dirty = True
        return True

    def _resolve(self, layer: str | Layer | None) -> Layer:
        if layer is None:
            return self.base
        if isinstance(layer, Layer):
            return layer
        found = self.get_layer(layer)
        if found is None:
            raise KeyError(f"no such layer: {layer!r}")
        return found

    def _on_layer_dirty(self, _layer: Layer) -> None:
        self._dirty = True
        if len(self._layers) > 1:
            self._layers.sort(key=lambda item: item.z)

    # ------------------------------------------------------------------
    # Drawing API — put / locate / move / remove by x,y
    # ------------------------------------------------------------------
    def put(
        self,
        x: int,
        y: int,
        content: str,
        style: Style | dict | None = None,
        /,
        *,
        layer: str | Layer | None = None,
        id: str | None = None,  # noqa: A002
        wrap: int | None = None,
        align: str = "left",
        clip: bool | None = None,
        visible: bool = True,
        z: int = 0,
        **kwargs: object,
    ) -> TextObject:
        """Put text at ``(x, y)``. Returns a handle you can move or remove.

        >>> s = Screen(Config(quiet=True, width=20, height=4))
        >>> obj = s.put(2, 1, "hi", fg="red")
        >>> obj.move(5, 1); s.remove(obj)   # doctest: +ELLIPSIS
        <TextObject ...>
        """
        return self._resolve(layer).put(
            x, y, content, style, id=id, visible=visible, clip=clip,
            wrap=wrap, align=align, z=z, base=self.config.default_style, **kwargs,
        )

    def put_runs(
        self,
        x: int,
        y: int,
        runs: Sequence[tuple[str, Style | dict | None]],
        /,
        *,
        layer: str | Layer | None = None,
        id: str | None = None,  # noqa: A002
        **kwargs: object,
    ) -> TextObject:
        """Put mixed-style text: ``[("HP ", dim), ("30", red)]``."""
        target = self._resolve(layer)
        base = self.config.default_style
        return target.put_runs(x, y, runs, id=id, base=base, **kwargs)  # type: ignore[arg-type]

    def paint(
        self,
        x: int,
        y: int,
        content: str,
        style: Style | dict | None = None,
        /,
        *,
        layer: str | Layer | None = None,
        wrap: bool = False,
        **kwargs: object,
    ) -> int:
        """Paint raw cells at ``(x, y)`` (no handle — erase by rectangle).

        Use :meth:`put` when you want to remove or move the text later.
        """
        target = self._resolve(layer)
        st = normalize_style(style, base=self.config.default_style, **kwargs)
        used = target.paint.put(x, y, content, st, wrap=wrap)
        self._dirty = True
        return used

    def paint_runs(
        self,
        x: int,
        y: int,
        runs: Sequence[tuple[str, Style | dict | None]],
        /,
        *,
        layer: str | Layer | None = None,
    ) -> int:
        """Paint raw mixed-style runs without creating an object.

        The bulk-drawing counterpart of :meth:`put_runs`: nothing is
        registered, so it costs no bookkeeping and is erased by rectangle.
        """
        used = self._resolve(layer).paint_runs(x, y, runs)
        self._dirty = True
        return used

    def fill(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        char: str = " ",
        style: Style | dict | None = None,
        /,
        *,
        layer: str | Layer | None = None,
        **kwargs: object,
    ) -> int:
        """Fill a rectangle with ``char``."""
        target = self._resolve(layer)
        used = target.paint.fill(x, y, w, h, char, normalize_style(style, base=self.config.default_style, **kwargs))
        self._dirty = True
        return used

    def erase(
        self,
        x: int,
        y: int,
        w: int = 1,
        h: int = 1,
        *,
        layer: str | Layer | None = None,
    ) -> int:
        """Remove the cells in a rectangle (they become transparent)."""
        used = self._resolve(layer).paint.erase(x, y, w, h)
        self._dirty = True
        return used

    def remove(
        self,
        target: str | TextObject | tuple[int, int] | int,
        x: int | None = None,
        y: int | None = None,
        *,
        layer: str | Layer | None = None,
        width: int | None = None,
        height: int = 1,
    ) -> bool:
        """Remove text by id, by handle, or by position.

        ``remove("score")`` — by id
        ``remove(obj)`` — by handle
        ``remove((4, 2))`` or ``remove(4, 2)`` — whatever sits at that cell
        ``remove(4, 2, width=10)`` — clear a raw rectangle at that position
        """
        target_layer = self._resolve(layer)
        if isinstance(target, int) and x is not None:
            if width is not None:
                target_layer.paint.erase(target, x, width, height)
                self._dirty = True
                return True
            return self.remove((target, x), layer=target_layer)
        if isinstance(target, tuple):
            px, py = int(target[0]), int(target[1])
            if width is not None:
                target_layer.paint.erase(px, py, width, height)
                self._dirty = True
                return True
            return target_layer.remove_at(px, py)
        if isinstance(target, TextObject):
            (target.layer if layer is None else target_layer).remove(target)
            return True
        if width is not None and x is not None and y is not None:
            target_layer.paint.erase(x, y, width, height)
            self._dirty = True
            return True
        return target_layer.remove(str(target))

    def at(self, x: int, y: int, *, layer: str | Layer | None = None) -> TextObject | None:
        """Locate the object covering cell ``(x, y)`` (searches all layers)."""
        if layer is not None:
            return self._resolve(layer).at(x, y)
        found: TextObject | None = None
        for candidate in self._layers:
            if not candidate.visible:
                continue
            obj = candidate.at(x - candidate.offset[0], y - candidate.offset[1])
            if obj is not None and (found is None or candidate.z >= found.layer.z):
                found = obj
        return found

    def cell(self, x: int, y: int) -> Cell:
        """The composited cell currently at ``(x, y)``."""
        return self._composite_snapshot().get(x, y)

    def char(self, x: int, y: int) -> str:
        return self.cell(x, y).printable

    def find(self, content: str, *, layer: str | Layer | None = None) -> list[TextObject]:
        """Locate every *object* whose text contains ``content``."""
        if layer is not None:
            return self._resolve(layer).find(content)
        out: list[TextObject] = []
        for candidate in self._layers:
            out.extend(candidate.find(content))
        return out

    def find_all(self, content: str) -> list[Match]:
        """Locate ``content`` anywhere on screen — objects *and* raw cells.

        Returns :class:`Match` records carrying the position, the matched
        text, the layer and (when it is an object) the handle to remove it.
        """
        matches: list[Match] = []
        for candidate in self._layers:
            if not candidate.visible:
                continue
            ox, oy = candidate.offset
            for obj in candidate.find(content):
                rx, ry, _w, _h = obj.rect()
                matches.append(Match(obj.text, rx + ox, ry + oy, candidate.name, obj))
            for x, y in candidate.paint.find(content):
                matches.append(Match(content, x + ox, y + oy, candidate.name, None))
        matches.sort(key=lambda m: (m.y, m.x))
        return matches

    def text_at(self, x: int, y: int) -> Match | None:
        """Locate the text run covering cell ``(x, y)``, object or raw.

        Raw runs are grown left and right until a blank cell, so asking about
        any character of ``"world"`` returns the whole word and its origin.
        """
        obj = self.at(x, y)
        if obj is not None:
            rx, ry, _w, _h = obj.rect()
            return Match(obj.text, rx + obj.layer.offset[0], ry + obj.layer.offset[1], obj.layer.name, obj)

        snapshot = self._composite_snapshot()
        bounds = snapshot.run_bounds(x, y)
        if bounds is None:
            return None
        start_x, end_x = bounds
        text = "".join(
            snapshot.get(cx, y).char for cx in range(start_x, end_x + 1)
            if snapshot.get(cx, y).char != CONTINUATION
        )
        return Match(text, start_x, y, None, None)

    def objects(self, *, layer: str | Layer | None = None) -> list[TextObject]:
        """Every text object, in draw order."""
        if layer is not None:
            return self._resolve(layer).objects()
        out: list[TextObject] = []
        for candidate in self._layers:
            out.extend(candidate.objects())
        return out

    def clear(self, *, layer: str | Layer | None = None, objects: bool = True) -> None:
        """Clear a layer (default: the base layer, objects included)."""
        if layer is None:
            self.base.clear(objects=objects)
        else:
            self._resolve(layer).clear(objects=objects)
        self._dirty = True

    def rect(self, x: int, y: int, w: int, h: int, style: Style | dict | None = None, /, *,
             layer: str | Layer | None = None, chars: str | BoxChars | None = None,
             fill: str | None = None, fill_style: Style | dict | None = None,
             title: str = "", title_align: str = "left", title_style: Style | dict | None = None,
             **kwargs: object) -> None:
        """Draw a box outline (optionally filled and titled)."""
        chars = chars if chars is not None else self.box_chars
        st = normalize_style(style, base=self.config.default_style, **kwargs)
        self._resolve(layer).paint.rect(
            x, y, w, h, st,
            chars=chars,
            fill=fill,
            fill_style=normalize_style(fill_style, base=st) if fill_style is not None else st,
            title=title,
            title_align=title_align,
            title_style=normalize_style(title_style, base=st) if title_style is not None else None,
        )
        self._dirty = True

    box = rect

    def hline(self, x: int, y: int, length: int, char: str = "─", style: Style | None = None, *,
              layer: str | Layer | None = None) -> int:
        used = self._resolve(layer).paint.hline(x, y, length, char, normalize_style(style, base=self.config.default_style))
        self._dirty = True
        return used

    def vline(self, x: int, y: int, length: int, char: str = "│", style: Style | None = None, *,
              layer: str | Layer | None = None) -> int:
        used = self._resolve(layer).paint.vline(x, y, length, char, normalize_style(style, base=self.config.default_style))
        self._dirty = True
        return used

    def line(self, x0: int, y0: int, x1: int, y1: int, char: str = "•", style: Style | None = None, *,
             layer: str | Layer | None = None) -> int:
        used = self._resolve(layer).paint.line(x0, y0, x1, y1, char, normalize_style(style, base=self.config.default_style))
        self._dirty = True
        return used

    def blit(self, buffer: Buffer, x: int = 0, y: int = 0, *, layer: str | Layer | None = None,
             transparent: bool = True) -> None:
        """Stamp another buffer (or a widget's output) onto the screen."""
        self._resolve(layer).paint.blit(buffer, x, y, transparent=transparent)
        self._dirty = True

    def scroll(self, lines: int = 1, *, layer: str | Layer | None = None, columns: int = 0) -> None:
        """Scroll a layer's raw content (negative = up). Objects stay put."""
        self._resolve(layer).paint.scroll(lines, columns)
        self._dirty = True

    # ------------------------------------------------------------------
    # Cursor / window
    # ------------------------------------------------------------------
    def set_cursor(self, x: int | None, y: int | None = None) -> None:
        """Show the hardware cursor at ``(x, y)``, or hide it with ``None``."""
        self.config.cursor = None if x is None else (int(x), int(y if y is not None else 0))
        self._dirty = True

    def send(self, data: str) -> None:
        """Write a raw escape sequence (OSC 52, custom modes, ...) now.

        Use this for anything the API does not model directly. It respects
        :attr:`Config.quiet` and batches like every other write.
        """
        self._write(data)

    def copy_to_clipboard(self, text: str, selection: str = "c") -> None:
        """Send OSC 52 so the terminal puts ``text`` on the system clipboard."""
        self.send(ansi.clipboard(text, selection))

    def set_title(self, title: str | None) -> None:
        self.config.title = title
        if self._started and self._interactive and title is not None:
            self._write(ansi.title(title))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def stream(self):
        """The output stream in use."""
        if self._stream is None:
            self._stream = self.config.output if self.config.output is not None else sys.stdout
        return self._stream

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> Screen:
        """Take over the terminal and paint the first frame."""
        if self._started:
            return self
        self._depth = self.config.resolved_depth()
        self._interactive = is_tty(self.stream) and not self.config.quiet
        self._width, self._height = self.config.resolved_size()
        self._apply_size(self._width, self._height, resize_layers=True)

        if self._interactive:
            sequence: list[str] = []
            cfg = self.config
            if cfg.alt_screen:
                sequence.append(ansi.ALT_SCREEN_ON)
                if cfg.clear_scrollback:
                    sequence.append(ansi.CLEAR_SCROLLBACK)
            if not cfg.line_wrap:
                sequence.append(ansi.LINE_WRAP_OFF)
            if cfg.mouse:
                sequence.append(ansi.MOUSE_CLICK_ON)
                if cfg.mouse_motion:
                    sequence.append(ansi.MOUSE_MOTION_ON)
                sequence.append(ansi.MOUSE_SGR_ON)
            if cfg.focus_events:
                sequence.append(ansi.FOCUS_ON)
            if cfg.bracketed_paste:
                sequence.append(ansi.BRACKETED_PASTE_ON)
            if cfg.application_keys:
                sequence.append(ansi.APPLICATION_KEYS_ON)
            if cfg.hide_cursor and cfg.cursor is None:
                sequence.append(ansi.HIDE_CURSOR)
            if cfg.cursor_shape != "default":
                sequence.append(ansi.cursor_shape(cfg.cursor_shape))
            if cfg.title:
                sequence.append(ansi.title(cfg.title))
            self._write("".join(part for part in sequence if part))
            self.keyboard.timeout = cfg.input_timeout
            self.keyboard.start()
            self._install_signal()
            atexit.register(self.stop)

        self._started = True
        self._first = True
        self.draw(force=True)
        return self

    def stop(self) -> None:
        """Give the terminal back exactly as we found it."""
        if not self._started:
            return
        cfg = self.config
        if self._interactive:
            sequence: list[str] = []
            if cfg.mouse:
                sequence += [ansi.MOUSE_SGR_OFF, ansi.MOUSE_ANY_OFF, ansi.MOUSE_CLICK_OFF]
            if cfg.focus_events:
                sequence.append(ansi.FOCUS_OFF)
            if cfg.bracketed_paste:
                sequence.append(ansi.BRACKETED_PASTE_OFF)
            if cfg.application_keys:
                sequence.append(ansi.APPLICATION_KEYS_OFF)
            if not cfg.line_wrap:
                sequence.append(ansi.LINE_WRAP_ON)
            sequence.append(ansi.SHOW_CURSOR)
            sequence.append(ansi.cursor_shape("default"))
            if cfg.clear_on_exit and not cfg.alt_screen:
                sequence.append(ansi.CLEAR_SCREEN_AND_HOME)
            if cfg.alt_screen:
                sequence.append(ansi.ALT_SCREEN_OFF)
            if cfg.restore_on_exit:
                sequence.append(ansi.FULL_RESET)
            self._write("".join(sequence))
            self.keyboard.stop()
        self._uninstall_signal()
        with contextlib.suppress(Exception):
            atexit.unregister(self.stop)
        self._started = False
        self._cursor_state = None

    close = stop

    def __enter__(self) -> Screen:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def __repr__(self) -> str:
        state = "started" if self._started else "idle"
        return f"<Screen {self._width}x{self._height} layers={len(self._layers)} {state}>"

    # ------------------------------------------------------------------
    # Resize
    # ------------------------------------------------------------------
    def _install_signal(self) -> None:
        if not self.config.follow_resize or not hasattr(signal, "SIGWINCH"):
            return
        try:
            self._sigwinch_prev = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, self._sigwinch)
        except (ValueError, OSError):  # not the main thread
            self._sigwinch_prev = None

    def _uninstall_signal(self) -> None:
        if self._sigwinch_prev is None or not hasattr(signal, "SIGWINCH"):
            return
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGWINCH, self._sigwinch_prev)  # type: ignore[arg-type]
        self._sigwinch_prev = None

    def _sigwinch(self, *_args: object) -> None:
        cols, rows = terminal_size()
        self._pending_resize = (cols, rows)

    def resize(self, width: int | None = None, height: int | None = None) -> bool:
        """Resize the screen. Returns True when the size actually changed."""
        if width is None or height is None:
            detected = terminal_size()
            width = self.config.width if self.config.width is not None else detected[0]
            height = self.config.height if self.config.height is not None else detected[1]
        width = max(self.config.min_width, int(width))
        height = max(self.config.min_height, int(height))
        if (width, height) == (self._width, self._height):
            return False
        self._apply_size(width, height, resize_layers=True)
        self._first = True
        self._dirty = True
        return True

    def _apply_size(self, width: int, height: int, *, resize_layers: bool) -> None:
        self._width, self._height = width, height
        self._back = Buffer(width, height)
        self._front = Buffer(width, height)
        self._composited = False
        if resize_layers:
            for layer in self._layers:
                if layer.width != width or layer.height != height:
                    layer.resize(width, height)
        self._dirty = True

    def _poll_resize(self) -> None:
        pending = self._pending_resize
        self._pending_resize = None
        if pending and self.config.follow_resize and self.config.width is None:
            self.resize(*pending)
        elif self.config.follow_resize and self.config.width is None and self._interactive:
            cols, rows = terminal_size()
            if (cols, rows) != (self._width, self._height):
                self.resize(cols, rows)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def poll(self, timeout: float | None = None) -> list[Event]:
        """Read input events (keys, mouse, paste, focus, resize)."""
        events: list[Event] = list(self._events)
        self._events.clear()
        if self._interactive:
            events.extend(self.keyboard.read(timeout))
        self._poll_resize()
        return events

    def wait(self, timeout: float = -1) -> list[Event]:
        """Block until at least one event arrives."""
        events = self.poll(timeout)
        if events:
            return events
        return self.poll(timeout if timeout >= 0 else 0.05)

    # ------------------------------------------------------------------
    # Compositing & painting
    # ------------------------------------------------------------------
    def _composite_snapshot(self) -> Buffer:
        """The current composited screen, for queries and off-screen capture.

        After :meth:`draw` swaps the buffers the composited content lives in
        ``_front``; when something changed since, composite fresh.
        """
        if not self._dirty and self._composited:
            return self._front
        snapshot = Buffer(self._width, self._height)
        self._composite_into(snapshot)
        return snapshot

    def _composite_into(self, target: Buffer) -> None:
        cfg = self.config
        target.clear(char=cfg.background or "", style=cfg.background_style)
        for layer in self._layers:
            layer.compose_into(target)

    def _composite(self) -> None:
        started = time.perf_counter() if self.config.stats else 0.0
        self._composite_into(self._back)
        if self.config.stats:
            cost = time.perf_counter() - started
            self.stats.composites += cost
            self.stats.last_composite = cost

    def draw(self, *, force: bool = False) -> int:
        """Composite and paint. Returns the number of cells updated."""
        if force:
            self._first = True
            self._dirty = True
        if not self._dirty and not self._first:
            self._sync_cursor()
            return 0

        started = time.perf_counter() if self.config.stats else 0.0
        self._composite()
        self._dirty = False
        self._composited = True

        payload, changed = (self._paint_all() if (self._first or not self.config.diff) else self._paint_diff())
        self._first = False
        self._front, self._back = self._back, self._front

        payload += self._cursor_sequence()
        self._write(payload)

        if self.config.stats:
            cost = time.perf_counter() - started
            self.stats.frames += 1
            self.stats.draws += cost
            self.stats.last_draw = cost
            self.stats.cells_changed += changed
            self.stats.last_cells = changed
            self.stats.bytes_written += len(payload)
            self.stats.last_bytes = len(payload)
        return changed

    #: Aliases, for whichever verb reads best in your loop.
    render = draw
    present = draw

    def _paint_diff(self) -> tuple[str, int]:
        """Emit only the cells that changed since the last frame.

        Changed cells are collected in one C-level pass, grouped into
        horizontal runs, and each run becomes a single cursor move followed by
        the characters (with style deltas). Runs that reach the end of a row
        collapse their trailing blanks into one "clear to end of line".
        """
        cfg = self.config
        depth = self._depth
        back, front = self._back, self._front
        bc, fc = back._cells, front._cells  # noqa: SLF001
        w = back.width
        last_col = w - 1

        changed = [
            (i, b)
            for i, (b, f) in enumerate(zip(bc, fc, strict=False))
            if b is not f and b != f
        ]
        if not changed:
            return "", 0

        parts: list[str] = [ansi.SYNC_BEGIN] if cfg.sync_output else []
        style: Style | None = self._term_style
        cells_changed = 0

        run: list[Cell] = []
        first_index = 0
        previous = -2
        for i, cell in changed:
            if run and (i != previous + 1 or i // w != previous // w):
                self._emit_run(first_index, run, parts, style, depth, cfg, w, last_col)
                style, cells_changed = self._run_style, cells_changed + self._run_cells
                run = []
            if not run:
                first_index = i
            run.append(cell)
            previous = i
        if run:
            self._emit_run(first_index, run, parts, style, depth, cfg, w, last_col)
            style, cells_changed = self._run_style, cells_changed + self._run_cells

        if cfg.sync_output:
            parts.append(ansi.SYNC_END)
        self._term_style = style
        return "".join(parts), cells_changed

    def _emit_run(
        self,
        first_index: int,
        run: list[Cell],
        parts: list[str],
        style: Style | None,
        depth: ColorDepth,
        cfg: Config,
        w: int,
        last_col: int,
    ) -> None:
        """Append one horizontal run of changed cells to ``parts``."""
        end = len(run)
        tail = ""
        if cfg.trim_rows and (first_index + end - 1) % w == last_col:
            while end and _trimmable(run[end - 1]):
                end -= 1
            if end < len(run):
                tail = ansi.CLEAR_TO_EOL

        parts.append(ansi.move(first_index % w, first_index // w))
        count = 0
        for cell in run[:end]:
            if cell.char == CONTINUATION:
                continue
            if cell.style is not style and cell.style != style:
                parts.append(cell.style.diff(style, depth))
                style = cell.style
            parts.append(cell.printable)
            count += 1
        if tail:
            parts.append(tail)
        self._run_style = style
        self._run_cells = count + (len(run) - end)

    def _paint_all(self) -> tuple[str, int]:
        """Full repaint, row by row, with trailing blanks collapsed to EL."""
        cfg = self.config
        depth = self._depth
        back = self._back
        bc = back._cells  # noqa: SLF001
        w, h = back.width, back.height
        parts: list[str] = [ansi.SYNC_BEGIN] if cfg.sync_output else []
        style: Style | None = self._term_style
        changed = 0

        for y in range(h):
            row = bc[y * w:(y + 1) * w]
            end = w
            if cfg.trim_rows:
                while end and _trimmable(row[end - 1]):
                    end -= 1
            parts.append(ansi.move(0, y))
            for k in range(end):
                cell = row[k]
                if cell.char == CONTINUATION:
                    continue
                if cell.style is not style and cell.style != style:
                    parts.append(cell.style.diff(style, depth))
                    style = cell.style
                parts.append(cell.printable)
                changed += 1
            if end < w:
                parts.append(ansi.CLEAR_TO_EOL)
        if cfg.sync_output:
            parts.append(ansi.SYNC_END)
        self._term_style = style
        return "".join(parts), changed

    def _cursor_sequence(self) -> str:
        cfg = self.config
        if cfg.cursor is not None:
            x, y = cfg.cursor
            x = max(0, min(self._width - 1, x))
            y = max(0, min(self._height - 1, y))
            out = ""
            if self._cursor_state is not True:
                out += ansi.SHOW_CURSOR
                self._cursor_state = True
            if self._cursor_pos != (x, y):
                out += ansi.move(x, y)
                self._cursor_pos = (x, y)
            return out
        if cfg.hide_cursor and self._interactive and self._cursor_state is not False:
            self._cursor_state = False
        return ""

    def _sync_cursor(self) -> None:
        if self.config.cursor is not None and self._cursor_pos != tuple(self.config.cursor):
            self._write(self._cursor_sequence())

    def _write(self, data: str) -> None:
        if not data or self.config.quiet:
            return
        stream = self.stream
        chunk = self.config.batch_writes
        try:
            if chunk and len(data) > chunk:
                for offset in range(0, len(data), chunk):
                    stream.write(data[offset:offset + chunk])  # type: ignore[attr-defined]
            else:
                stream.write(data)  # type: ignore[attr-defined]
            if self.config.flush:
                stream.flush()  # type: ignore[attr-defined]
        except (ValueError, OSError):  # closed stream / broken pipe
            pass

    # ------------------------------------------------------------------
    # Off-screen output (tests, captures, previews)
    # ------------------------------------------------------------------
    def frame(self, *, rstrip: bool = True) -> str:
        """The composited screen as plain text (no escapes)."""
        buffer = self._composite_snapshot()
        return buffer.text(rstrip=rstrip)

    def frame_ansi(self, *, rstrip: bool = True) -> str:
        """The composited screen as an ANSI string."""
        buffer = self._composite_snapshot()
        return buffer.ansi(rstrip=rstrip, depth=self._depth)

    screenshot = frame_ansi

    def buffer(self) -> Buffer:
        """A copy of the composited back buffer."""
        return self._composite_snapshot().copy()


__all__ = ["Match", "Screen"]

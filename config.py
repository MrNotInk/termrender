"""Every knob the engine exposes, in one place.

Nothing is hard-coded: geometry, terminal modes, cursor, mouse, colour depth,
diffing, synchronisation, timing, I/O and shutdown behaviour are all fields of
:class:`Config`. Pass one to :class:`~termrender.screen.Screen`, or build a
screen and mutate ``screen.config`` before :meth:`Screen.start`.

    from termrender import Screen, Config, ColorDepth

    cfg = Config(alt_screen=True, color_depth=ColorDepth.TRUECOLOR, max_fps=60)
    screen = Screen(cfg)

Individual values can also be passed straight to ``Screen(...)`` as keyword
arguments — they are merged over the config defaults.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field, fields, replace
from typing import Any, TextIO

from .style import ColorDepth, Style

#: Fallback size when the terminal cannot be queried (pipes, CI, TERM=dumb).
FALLBACK_SIZE = (80, 24)


def terminal_size(fallback: tuple[int, int] = FALLBACK_SIZE) -> tuple[int, int]:
    """Current ``(columns, rows)``, tolerating non-tty output."""
    try:
        size = shutil.get_terminal_size(fallback)
        if size.columns > 0 and size.lines > 0:
            return size.columns, size.lines
    except (OSError, ValueError):  # pragma: no cover - unusual environments
        pass
    return fallback


def is_tty(stream: Any = None) -> bool:
    """True when ``stream`` (default stdout) is attached to a terminal."""
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


@dataclass
class Config:
    """Complete configuration of a rendering session."""

    # -- geometry --------------------------------------------------------
    width: int | None = None
    """Fixed width in cells. ``None`` = auto-detect and follow resizes."""

    height: int | None = None
    """Fixed height in cells. ``None`` = auto-detect and follow resizes."""

    min_width: int = 1
    """Clamp for auto-detected width."""

    min_height: int = 1
    """Clamp for auto-detected height."""

    max_width: int | None = None
    """Optional upper clamp (useful for very wide terminals)."""

    max_height: int | None = None
    """Optional upper clamp."""

    follow_resize: bool = True
    """Re-layout automatically when the terminal window changes size."""

    # -- terminal modes --------------------------------------------------
    alt_screen: bool = True
    """Use the alternate screen buffer (your shell scrollback is preserved)."""

    hide_cursor: bool = True
    """Hide the hardware cursor while rendering."""

    cursor: tuple[int, int] | None = None
    """When set, show the cursor at this ``(x, y)`` instead of hiding it."""

    cursor_shape: str = "default"
    """``default | block | block-blink | underline | underline-blink | bar | bar-blink``."""

    mouse: bool = False
    """Capture mouse clicks / wheel (SGR encoding)."""

    mouse_motion: bool = False
    """Also report mouse movement (implies :attr:`mouse`)."""

    focus_events: bool = False
    """Report terminal focus gained/lost."""

    bracketed_paste: bool = False
    """Report pasted text as a single event instead of keystrokes."""

    application_keys: bool = True
    """Application cursor-key mode (needed for reliable arrow key parsing)."""

    line_wrap: bool = False
    """Allow the terminal's own auto-wrap during rendering (usually off)."""

    title: str | None = None
    """Window / tab title to set while the screen is open."""

    clear_scrollback: bool = False
    """Erase scrollback history on start (alt-screen sessions only)."""

    # -- rendering -------------------------------------------------------
    color_depth: ColorDepth = ColorDepth.AUTO
    """``AUTO | NONE | BASIC | EXTENDED | TRUECOLOR``."""

    default_style: Style = field(default_factory=Style)
    """Style applied to the whole screen and inherited by layers/objects."""

    background: str = ""
    """Character used for cells nothing painted over.

    ``""`` (default) leaves untouched cells empty: they render as spaces but
    cost no bytes and stay transparent for :meth:`Screen.frame_ansi`. Set it
    to ``" "`` plus a ``background_style`` for a full-bleed backdrop.
    """

    background_style: Style | None = None
    """Style of the background character (defaults to :attr:`default_style`)."""

    diff: bool = True
    """Only redraw cells that changed. ``False`` repaints everything each frame."""

    sync_output: bool = True
    """Wrap each frame in a synchronized-update block (prevents tearing)."""

    batch_writes: int = 0
    """Split frame output into chunks of at most N bytes (0 = write it all at once)."""

    flush: bool = True
    """Flush the output stream at the end of every frame."""

    unicode: bool = True
    """Use Unicode box drawing; ``False`` falls back to ASCII ``+ - |``."""

    trim_rows: bool = True
    """Skip trailing spaces on each row when emitting (fewer bytes)."""

    # -- timing ----------------------------------------------------------
    max_fps: float | None = None
    """Frame-rate cap for :meth:`~termrender.app.App.run` (None = uncapped)."""

    input_timeout: float = 0.0
    """Seconds to wait for input: ``0`` = poll, ``-1`` = block forever."""

    # -- I/O -------------------------------------------------------------
    output: TextIO | None = None
    """Where escapes go. ``None`` = ``sys.stdout`` (or a string sink when quiet)."""

    quiet: bool = False
    """Never touch the terminal; only build frames in memory (tests, captures)."""

    restore_on_exit: bool = True
    """Reset terminal modes when the screen closes (also on crash/atexit)."""

    clear_on_exit: bool = True
    """Clear the screen on exit (alt-screen off already restores the old one)."""

    stats: bool = False
    """Collect per-frame timing / byte counters in ``screen.stats``."""

    def __post_init__(self) -> None:
        if isinstance(self.color_depth, str):
            self.color_depth = ColorDepth(self.color_depth.lower())
        if isinstance(self.default_style, dict):
            self.default_style = Style.parse(**self.default_style)
        if isinstance(self.background_style, dict):
            self.background_style = Style.parse(**self.background_style)
        if self.background_style is None:
            self.background_style = self.default_style
        if self.mouse_motion:
            self.mouse = True
        if self.quiet:
            self.restore_on_exit = False
        if self.min_width < 1 or self.min_height < 1:
            raise ValueError("min_width / min_height must be >= 1")
        for name in ("width", "height"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} must be >= 1 or None")

    # -- helpers ---------------------------------------------------------
    @classmethod
    def from_env(cls, **overrides: Any) -> Config:
        """Build a config from the environment (``TERM``, ``COLORTERM``, ``NO_COLOR``)."""
        cfg = cls(color_depth=ColorDepth.detect())
        term = os.environ.get("TERM", "")
        if term in ("dumb", "") or not is_tty():
            cfg.quiet = True
        return cfg.replace(**overrides)

    def replace(self, **changes: Any) -> Config:
        """Return a copy with fields replaced."""
        return replace(self, **changes)

    def resolved_size(self) -> tuple[int, int]:
        """The size this config implies, querying the terminal when needed."""
        cols, rows = terminal_size()
        w = self.width if self.width is not None else cols
        h = self.height if self.height is not None else rows
        w = max(self.min_width, w if w > 0 else FALLBACK_SIZE[0])
        h = max(self.min_height, h if h > 0 else FALLBACK_SIZE[1])
        if self.max_width:
            w = min(w, self.max_width)
        if self.max_height:
            h = min(h, self.max_height)
        return w, h

    def resolved_depth(self) -> ColorDepth:
        """The concrete colour depth in use (AUTO resolved)."""
        if self.color_depth is ColorDepth.AUTO:
            return ColorDepth.detect()
        return self.color_depth

    def to_dict(self) -> dict[str, Any]:
        """Round-trippable dict view (styles stay as objects, depth as a name)."""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["color_depth"] = self.color_depth.value
        data.pop("output", None)
        return data

    def to_json(self) -> dict[str, Any]:
        """Fully serialisable view (styles rendered as strings)."""
        data = self.to_dict()
        for key in ("default_style", "background_style"):
            value = data.get(key)
            if isinstance(value, Style):
                data[key] = str(value)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Rebuild a config from :meth:`to_dict` (unknown keys are ignored)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def describe(self) -> str:
        """Human-readable dump of every setting."""
        width = max(len(f.name) for f in fields(self) if f.name != "output")
        lines = []
        for f in fields(self):
            if f.name == "output":
                continue
            value = getattr(self, f.name)
            if isinstance(value, ColorDepth):
                value = value.value
            elif isinstance(value, Style):
                value = str(value) or "default"
            lines.append(f"{f.name:<{width}}  {value!r}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return f"Config({', '.join(f'{k}={v!r}' for k, v in list(self.to_dict().items())[:4])}, …)"


@dataclass
class Stats:
    """Per-frame counters, populated when ``Config.stats`` is on."""

    frames: int = 0
    cells_changed: int = 0
    bytes_written: int = 0
    draws: float = 0.0  # total seconds spent inside draw()
    composites: float = 0.0  # total seconds spent compositing layers
    last_draw: float = 0.0
    last_composite: float = 0.0
    last_bytes: int = 0
    last_cells: int = 0
    fps: float = 0.0

    @property
    def avg_draw(self) -> float:
        return (self.draws / self.frames * 1000) if self.frames else 0.0

    @property
    def avg_bytes(self) -> float:
        return (self.bytes_written / self.frames) if self.frames else 0.0

    def summary(self) -> str:
        return (
            f"frames={self.frames} fps={self.fps:.1f} changed={self.last_cells} "
            f"bytes={self.last_bytes} draw={self.last_draw * 1000:.2f}ms "
            f"avg={self.avg_draw:.2f}ms"
        )


__all__ = ["FALLBACK_SIZE", "Config", "Stats", "is_tty", "terminal_size"]

"""termrender — turn the terminal into an addressable rendering engine.

Place text at ``(x, y)``, move it, find it, remove it, layer it, style it, and
let the engine emit only the bytes that actually changed.

Quick start
-----------
::

    from termrender import Screen

    with Screen() as screen:
        screen.put(4, 2, "hello", fg="cyan", bold=True, id="greeting")
        screen.draw()                      # paint the frame

        screen.at(5, 2).move(10, 5)        # locate by coordinate, then move
        screen.draw()

        screen.remove("greeting")          # remove by id
        screen.draw()

Headless (tests, captures, previews)::

    screen = Screen(quiet=True, width=40, height=10)
    screen.put(0, 0, "no terminal needed")
    print(screen.frame())

Everything is configurable — see :class:`~termrender.config.Config`.
"""

from __future__ import annotations

from . import ansi, widgets
from .app import App
from .buffer import Buffer
from .cell import Cell
from .chars import ASCII, DOUBLE, HEAVY, ROUNDED, SHARP, BoxChars, box_set
from .config import Config, Stats, is_tty, terminal_size
from .input import (
    Event,
    FocusEvent,
    Key,
    Keyboard,
    MouseEvent,
    Parser,
    PasteEvent,
    ResizeEvent,
)
from .layer import Layer
from .objects import TextObject
from .screen import Match, Screen
from .style import (
    BOLD,
    DIM,
    ITALIC,
    REVERSED,
    UNDERLINED,
    Color,
    ColorDepth,
    Style,
    Underline,
    normalize_style,
)
from .text import char_width, pad, text_width, truncate, wrap

__version__ = "1.0.0"
__author__ = "termrender contributors"
__license__ = "MIT"

#: Shortcuts so ``t.red`` / ``t.on_blue`` read nicely at call sites.
red = Color.ansi(1)
green = Color.ansi(2)
yellow = Color.ansi(3)
blue = Color.ansi(4)
magenta = Color.ansi(5)
cyan = Color.ansi(6)
white = Color.ansi(7)
black = Color.ansi(0)


def quick(width: int = 80, height: int = 24, **config: object) -> Screen:
    """A ready-to-use screen of a fixed size (no terminal takeover)."""
    return Screen(Config(width=width, height=height, **config))  # type: ignore[arg-type]


def render(width: int, height: int, draw_callback, **config: object) -> str:
    """One-shot render: ``draw_callback(screen)`` then return the ANSI frame."""
    screen = quick(width, height, quiet=True, **config)
    draw_callback(screen)
    return screen.frame_ansi()


__all__ = [
    "ASCII",
    "App",
    "BOLD",
    "Buffer",
    "Cell",
    "Color",
    "ColorDepth",
    "Config",
    "DIM",
    "DOUBLE",
    "Event",
    "FocusEvent",
    "HEAVY",
    "ITALIC",
    "Key",
    "Keyboard",
    "Layer",
    "Match",
    "MouseButton",
    "MouseEvent",
    "Parser",
    "PasteEvent",
    "REVERSED",
    "ROUNDED",
    "ResizeEvent",
    "SHARP",
    "Screen",
    "Stats",
    "Style",
    "TextObject",
    "UNDERLINED",
    "Underline",
    "ansi",
    "black",
    "blue",
    "box_set",
    "BoxChars",
    "char_width",
    "cyan",
    "green",
    "is_tty",
    "magenta",
    "normalize_style",
    "pad",
    "quick",
    "red",
    "render",
    "terminal_size",
    "text_width",
    "truncate",
    "white",
    "widgets",
    "wrap",
    "yellow",
    "__version__",
]

# ``MouseButton`` is an alias for readability in mouse handlers.
MouseButton = MouseEvent

"""Raw ANSI / VT escape sequences.

Everything the engine emits is assembled here so the rest of the package can
stay readable. All coordinates in this module are **0-based** (the +1 offset
needed by the terminal is applied inside the helpers).
"""

from __future__ import annotations

import base64
import re

ESC = "\x1b"
CSI = f"{ESC}["
OSC = f"{ESC}]"
BEL = "\x07"
ST = f"{ESC}\\"

# --------------------------------------------------------------------------
# Cursor
# --------------------------------------------------------------------------
HIDE_CURSOR = f"{CSI}?25l"
SHOW_CURSOR = f"{CSI}?25h"
SAVE_CURSOR = f"{ESC}7"
RESTORE_CURSOR = f"{ESC}8"


def move(x: int, y: int) -> str:
    """Cursor position (CUP): absolute move to column ``x``, row ``y``."""
    return f"{CSI}{y + 1};{x + 1}H"


def move_col(x: int) -> str:
    """Move to column ``x`` on the current row (CHA)."""
    return f"{CSI}{x + 1}G"


def move_row(y: int) -> str:
    """Move to row ``y`` on the current column (VPA)."""
    return f"{CSI}{y + 1}d"


def cursor_up(n: int = 1) -> str:
    return f"{CSI}{n}A"


def cursor_down(n: int = 1) -> str:
    return f"{CSI}{n}B"


def cursor_forward(n: int = 1) -> str:
    return f"{CSI}{n}C"


def cursor_back(n: int = 1) -> str:
    return f"{CSI}{n}D"


#: Cursor shapes for ``CSI <n> SP q`` (DECSCUSR).
CURSOR_SHAPES = {
    "default": 0,
    "block-blink": 1,
    "block": 2,
    "underline-blink": 3,
    "underline": 4,
    "bar-blink": 5,
    "bar": 6,
}


def cursor_shape(shape: str | int = "default") -> str:
    """Set the cursor glyph (block / underline / bar, blinking or steady)."""
    n = CURSOR_SHAPES[shape] if isinstance(shape, str) else int(shape)
    return f"{CSI}{n} q"


# --------------------------------------------------------------------------
# Screen / erase
# --------------------------------------------------------------------------
ALT_SCREEN_ON = f"{CSI}?1049h"
ALT_SCREEN_OFF = f"{CSI}?1049l"
CLEAR_SCREEN = f"{CSI}2J"
CLEAR_SCREEN_AND_HOME = f"{CSI}2J{CSI}H"
CLEAR_SCROLLBACK = f"{CSI}3J"
CLEAR_TO_END = f"{CSI}0J"
CLEAR_LINE = f"{CSI}2K"
CLEAR_TO_EOL = f"{CSI}0K"
SCROLL_UP = f"{CSI}1S"
SCROLL_DOWN = f"{CSI}1T"


def scroll_up(n: int = 1) -> str:
    return f"{CSI}{n}S"


def scroll_down(n: int = 1) -> str:
    return f"{CSI}{n}T"


def set_scroll_region(top: int, bottom: int) -> str:
    """DECSTBM, 0-based inclusive rows."""
    return f"{CSI}{top + 1};{bottom + 1}r"


RESET_SCROLL_REGION = f"{CSI}r"

# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------
SYNC_BEGIN = f"{CSI}?2026h"  # begin synchronized update (no tearing)
SYNC_END = f"{CSI}?2026l"  # end synchronized update
MOUSE_CLICK_ON = f"{CSI}?1000h"
MOUSE_CLICK_OFF = f"{CSI}?1000l"
MOUSE_MOTION_ON = f"{CSI}?1002h"  # button-event motion tracking
MOUSE_MOTION_OFF = f"{CSI}?1002l"
MOUSE_ANY_ON = f"{CSI}?1003h"  # any-event motion tracking
MOUSE_ANY_OFF = f"{CSI}?1003l"
MOUSE_SGR_ON = f"{CSI}?1006h"  # SGR extended coordinates
MOUSE_SGR_OFF = f"{CSI}?1006l"
FOCUS_ON = f"{CSI}?1004h"
FOCUS_OFF = f"{CSI}?1004l"
BRACKETED_PASTE_ON = f"{CSI}?2004h"
BRACKETED_PASTE_OFF = f"{CSI}?2004l"
APPLICATION_KEYS_ON = f"{CSI}?1h"
APPLICATION_KEYS_OFF = f"{CSI}?1l"
LINE_WRAP_ON = f"{CSI}?7h"
LINE_WRAP_OFF = f"{CSI}?7l"

RESET = f"{CSI}0m"
FULL_RESET = f"{RESET}{LINE_WRAP_ON}{SHOW_CURSOR}{RESET_SCROLL_REGION}"


def title(text: str) -> str:
    """Set the terminal window / tab title."""
    return f"{OSC}2;{text}{ST}"


def clipboard(text: str, selection: str = "c") -> str:
    """OSC 52: ask the terminal to put ``text`` on the system clipboard.

    ``selection`` is the usual X11 set — ``"c"`` clipboard, ``"p"`` primary,
    ``"s"`` select, ``"q"`` secondary. Works in iTerm2, kitty, alacritty,
    WezTerm, Windows Terminal and tmux (with ``set -g set-clipboard on``).
    """
    encoded = base64.b64encode(text.encode("utf-8", "replace")).decode("ascii")
    return f"{OSC}52;{selection};{encoded}{ST}"


def query_clipboard(selection: str = "c") -> str:
    """OSC 52 read request (the terminal replies with the same sequence)."""
    return f"{OSC}52;{selection};?{ST}"


def hyperlink(url: str, text: str, link_id: str = "") -> str:
    """OSC-8 clickable hyperlink wrapping ``text``."""
    ident = f"id={link_id};" if link_id else ""
    return f"{OSC}8;{ident}{url}{ST}{text}{OSC}8;;{ST}"


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------
QUERY_SIZE = f"{CSI}18t"  # report text area size in characters
QUERY_CURSOR = f"{CSI}6n"  # report cursor position -> CSI row;col R
QUERY_COLOR = f"{CSI}10;?{CSI}11;?"  # report fg/bg colours

_ANSI_RE = re.compile(r"\x1b\[[0-9;:?<> ]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()#;?]*[0-9A-Za-z]|\x1b.")


def strip_ansi(text: str) -> str:
    """Remove every escape sequence from ``text``."""
    return _ANSI_RE.sub("", text)


def ansi_len(text: str) -> int:
    """Visible length of ``text`` (escape sequences do not count)."""
    return len(strip_ansi(text))


__all__ = [
    name
    for name in dir()
    if not name.startswith("_") and name not in {"annotations", "re"}
]

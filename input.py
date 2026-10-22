"""Keyboard and mouse input: raw bytes in, typed events out.

The terminal delivers one undifferentiated byte stream. This module turns it
into :class:`Key`, :class:`MouseEvent`, :class:`PasteEvent`,
:class:`FocusEvent` and :class:`ResizeEvent` objects, handling UTF-8, CSI
sequences, modifier encoding and the classic "is this ESC alone or the start
of an arrow key?" race.
"""

from __future__ import annotations

import codecs
import os
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

#: Human names for the control bytes 0x00-0x1f.
_CTRL_NAMES = {
    0: "ctrl-space", 8: "backspace", 9: "tab", 10: "enter", 13: "enter",
    27: "escape", 127: "backspace",
}

_SPECIAL = {
    "A": "up", "B": "down", "C": "right", "D": "left",
    "E": "kp-begin", "F": "end", "H": "home", "Z": "shift-tab",
    "P": "f1", "Q": "f2", "R": "f3", "S": "f4",
}

_TILDE = {
    1: "home", 2: "insert", 3: "delete", 4: "end", 5: "pageup", 6: "pagedown",
    7: "home", 8: "end", 11: "f1", 12: "f2", 13: "f3", 14: "f4", 15: "f5",
    17: "f6", 18: "f7", 19: "f8", 20: "f9", 21: "f10", 23: "f11", 24: "f12",
    25: "f13", 26: "f14", 28: "f15", 29: "f16", 31: "f17", 32: "f18",
    33: "f19", 34: "f20",
}


@dataclass(frozen=True, slots=True)
class Key:
    """A key press.

    ``name`` is a canonical identifier: ``"a"``, ``"up"``, ``"enter"``,
    ``"f5"``, ``"ctrl-c"``, ``"shift-tab"``. ``char`` holds the literal
    character for printable keys (``None`` for things like arrows).
    """

    name: str
    char: str | None = None
    ctrl: bool = False
    alt: bool = False
    shift: bool = False

    def is_printable(self) -> bool:
        return self.char is not None and not self.ctrl and not self.alt

    def matches(self, *names: str) -> bool:
        """``key.matches("q", "ctrl-c", "escape")``."""
        return self.name in names

    def __str__(self) -> str:
        parts = []
        if self.ctrl and not self.name.startswith("ctrl-"):
            parts.append("ctrl")
        if self.alt and not self.name.startswith("alt-"):
            parts.append("alt")
        if self.shift and not self.name.startswith("shift-"):
            parts.append("shift")
        parts.append(self.name)
        return "+".join(parts)

    def __repr__(self) -> str:
        return f"Key({str(self)!r})"


@dataclass(frozen=True, slots=True)
class MouseEvent:
    """A mouse event, with cell coordinates."""

    action: str  # press | release | drag | motion
    button: str  # left | middle | right | wheel-up | wheel-down | none
    x: int = 0
    y: int = 0

    @property
    def is_wheel(self) -> bool:
        return self.button.startswith("wheel")

    @property
    def pos(self) -> tuple[int, int]:
        return self.x, self.y

    def __repr__(self) -> str:
        return f"MouseEvent({self.action}, {self.button}, {self.x}, {self.y})"


@dataclass(frozen=True, slots=True)
class PasteEvent:
    """Text delivered by a bracketed paste."""

    text: str

    def __repr__(self) -> str:
        shown = self.text if len(self.text) <= 20 else self.text[:19] + "…"
        return f"PasteEvent({shown!r})"


@dataclass(frozen=True, slots=True)
class FocusEvent:
    focused: bool

    def __repr__(self) -> str:
        return f"FocusEvent({self.focused})"


@dataclass(frozen=True, slots=True)
class ResizeEvent:
    width: int
    height: int

    def __repr__(self) -> str:
        return f"ResizeEvent({self.width}, {self.height})"


Event = Key | MouseEvent | PasteEvent | FocusEvent | ResizeEvent


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def _modifier(code: int) -> tuple[bool, bool, bool]:
    """Decode the ``1;n`` modifier parameter → (shift, alt, ctrl)."""
    if code <= 1:
        return False, False, False
    code -= 1
    return bool(code & 1), bool(code & 2), bool(code & 4)


class Parser:
    """Incremental byte-stream parser producing :data:`Event` objects."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pasting: list[str] | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def feed_bytes(self, data: bytes) -> list[Event]:
        """Feed raw terminal bytes, decoding UTF-8 incrementally.

        A multi-byte character split across two reads is buffered until it is
        complete instead of turning into a replacement character.
        """
        return self.feed(self._decoder.decode(bytes(data)))

    def feed(self, data: str) -> list[Event]:
        """Feed decoded text and return every complete event found."""
        self._buf.extend(data.encode("utf-8", "replace"))
        events: list[Event] = []
        while self._buf:
            event, consumed = self._next()
            if consumed == 0:
                break  # incomplete sequence; wait for more bytes
            del self._buf[:consumed]
            if event is not None:
                events.extend(event if isinstance(event, list) else [event])
        return events

    # -- internals -------------------------------------------------------
    def _next(self) -> tuple[Event | list[Event] | None, int]:
        buf = self._buf
        ch = buf[0]

        if self._pasting is not None:
            end = buf.find(b"\x1b[201~")
            if end < 0:
                return None, 0
            chunk = bytes(buf[:end]).decode("utf-8", "replace")
            self._pasting.append(chunk)
            text = "".join(self._pasting)
            self._pasting = None
            return PasteEvent(text), end + 6

        if ch == 0x1B:
            return self._escape()
        if ch < 0x20 or ch == 0x7F:
            return self._control(ch), 1
        if ch >= 0x80:
            return self._utf8()
        if ch == 0x20:  # space gets a name you can bind to
            return Key("space", " "), 1
        return Key(chr(ch), chr(ch)), 1

    def _control(self, byte: int) -> Event:
        if byte in (9,):
            return Key("tab", "\t")
        if byte in (10, 13):
            return Key("enter", "\n")
        if byte == 127 or byte == 8:
            return Key("backspace", "\x7f")
        if byte == 0:
            return Key("ctrl-space", " ", ctrl=True)
        if 1 <= byte <= 26:
            letter = chr(byte + 96)
            return Key(f"ctrl-{letter}", letter, ctrl=True)
        return Key(_CTRL_NAMES.get(byte, f"ctrl-{byte}"))

    def _utf8(self) -> tuple[Event | None, int]:
        first = self._buf[0]
        need = 2 if first < 0xE0 else 3 if first < 0xF0 else 4
        if len(self._buf) < need:
            return None, 0
        raw = bytes(self._buf[:need])
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return Key("unknown"), 1
        return Key(text, text), need

    def _escape(self) -> tuple[Event | list[Event] | None, int]:
        buf = self._buf
        if len(buf) == 1:
            # Lone ESC. The caller decides whether to wait for a follow-up.
            return Key("escape"), 1

        second = buf[1]
        if second == ord("["):
            return self._csi(2)
        if second == ord("O"):
            if len(buf) < 3:
                return None, 0
            name = _SPECIAL.get(chr(buf[2]))
            return (Key(name) if name else Key(f"ss3-{chr(buf[2])}")), 3
        if second in (ord("7"), ord("8")):
            return Key("save-cursor" if second == ord("7") else "restore-cursor"), 2
        # alt + <something>
        inner = Parser()
        inner._buf = bytearray(buf[1:])
        events, consumed = inner._next()
        if consumed == 0 or events is None:
            return Key("escape"), 1
        out = []
        for ev in events if isinstance(events, list) else [events]:
            if isinstance(ev, Key):
                out.append(Key(ev.name, ev.char, ev.ctrl, True, ev.shift))
            else:
                out.append(ev)
        return (out[0] if len(out) == 1 else out), consumed + 1

    def _csi(self, start: int) -> tuple[Event | list[Event] | None, int]:
        buf = self._buf
        i = start
        # bracketed paste start
        if bytes(buf[start:start + 4]) == b"200~":
            self._pasting = []
            return None, start + 4

        while i < len(buf):
            byte = buf[i]
            if 0x40 <= byte <= 0x7E:  # final byte
                params = bytes(buf[start:i]).decode("ascii", "replace")
                return self._final(chr(byte), params, i + 1)
            if byte < 0x20 or byte > 0x3F:
                return Key("escape"), 1
            i += 1
        return None, 0  # incomplete

    def _final(self, final: str, params: str, consumed: int) -> tuple[Event | None, int]:
        if final in ("M", "m") and params.startswith("<"):
            return self._mouse(params[1:], final, consumed)
        if final == "I":
            return FocusEvent(True), consumed
        if final == "O":
            return FocusEvent(False), consumed
        if final == "R" and not params.startswith("1;"):
            # CSI row;col R is a cursor position report. CSI 1;mod R is a
            # modified F3 — the leading 1 is what tells them apart.
            return None, consumed
        if final == "t" and ";" in params:  # window size report
            parts = params.split(";")
            if len(parts) >= 3 and parts[0] == "8":
                try:
                    return ResizeEvent(int(parts[2]), int(parts[1])), consumed
                except ValueError:
                    return None, consumed
            return None, consumed

        try:
            values = [int(p) if p else 1 for p in params.split(";")]
        except ValueError:
            return Key(f"csi-{params}{final}"), consumed

        if final == "~":
            code = values[0] if values else 0
            shift, alt, ctrl = _modifier(values[1] if len(values) > 1 else 1)
            name = _TILDE.get(code, f"key-{code}")
            return Key(name, None, ctrl, alt, shift), consumed

        if final in _SPECIAL:
            shift, alt, ctrl = _modifier(values[1] if len(values) > 1 else 1)
            name = _SPECIAL[final]
            if shift and name in ("up", "down", "left", "right", "home", "end", "tab"):
                name = f"shift-{name}" if name == "tab" else name
            return Key(name, None, ctrl, alt, shift), consumed

        return Key(f"csi-{params}{final}", None), consumed

    def _mouse(self, params: str, final: str, consumed: int) -> tuple[Event | None, int]:
        try:
            code, x, y = (int(p) for p in params.split(";")[:3])
        except ValueError:
            return None, consumed
        x -= 1
        y -= 1
        if code & 64:
            button = "wheel-up" if code & 1 == 0 else "wheel-down"
            if code & 2:
                button = "wheel-left" if code & 1 == 0 else "wheel-right"
            return MouseEvent("press", button, x, y), consumed

        button_no = code & 3
        button = {0: "left", 1: "middle", 2: "right", 3: "none"}[button_no]
        shift = bool(code & 4)
        alt = bool(code & 8)
        ctrl = bool(code & 16)
        if code & 32:
            action = "drag" if button != "none" else "motion"
        else:
            action = "release" if final == "m" else "press"
        event = MouseEvent(action, button, x, y)
        if shift or alt or ctrl:
            event = MouseEvent(f"{action}+{'shift' if shift else ''}{'alt' if alt else ''}{'ctrl' if ctrl else ''}".rstrip("+"), button, x, y)
        return event, consumed


# --------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------


@dataclass
class Keyboard:
    """Raw-mode terminal reader.

    ``Keyboard()`` degrades to a no-op reader when stdin is not a tty, so
    code can run unchanged under pipes, CI and test harnesses.
    """

    stream: object = None  # file object, defaults to sys.stdin
    timeout: float = 0.0  # seconds; 0 = non-blocking poll, -1 = block
    esc_timeout: float = 0.02  # how long to wait for bytes after a lone ESC
    parser: Parser = field(default_factory=Parser)
    _raw: bool = field(default=False, init=False, repr=False)
    _saved: tuple | None = field(default=None, init=False, repr=False)
    _fd: int = field(default=-1, init=False, repr=False)

    # -- terminal mode ---------------------------------------------------
    @property
    def available(self) -> bool:
        try:
            return bool(self.file.isatty())
        except (AttributeError, ValueError):
            return False

    @property
    def file(self):
        return self.stream if self.stream is not None else sys.stdin

    def start(self) -> Keyboard:
        """Put the terminal into raw (unbuffered, no-echo) mode."""
        if self._raw or not self.available:
            return self
        try:
            import termios
            import tty

            self._fd = self.file.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
            self._raw = True
        except (ImportError, OSError, ValueError):  # pragma: no cover - Windows
            pass
        return self

    def stop(self) -> None:
        """Restore the previous terminal mode."""
        if not self._raw:
            return
        try:
            import termios

            if self._saved is not None:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        except (ImportError, OSError):  # pragma: no cover
            pass
        self._raw = False
        self._saved = None

    def __enter__(self) -> Keyboard:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- reading ---------------------------------------------------------
    def read(self, timeout: float | None = None, *, max_events: int = 256) -> list[Event]:
        """Read everything currently available and return parsed events."""
        if not self.available:
            return []
        limit = self.timeout if timeout is None else timeout
        events: list[Event] = []
        data = self._read_chunk(limit)
        if not data:
            return events

        events.extend(self.parser.feed_bytes(data))

        # A trailing lone ESC may be the start of a sequence still in flight.
        while len(events) and isinstance(events[-1], Key) and events[-1].name == "escape" and self._pending():
            more = self._read_chunk(self.esc_timeout)
            if not more:
                break
            events.pop()
            events.extend(self.parser.feed_bytes(more))

        return events[:max_events]

    def _pending(self) -> bool:
        """True when the parser holds an incomplete escape sequence."""
        return bool(self.parser._buf)  # noqa: SLF001

    def _read_chunk(self, timeout: float) -> bytes:
        try:
            import select

            fd = self.file.fileno()
        except (ImportError, OSError, ValueError):  # pragma: no cover
            return b""

        wait = None if timeout is not None and timeout < 0 else max(0.0, timeout or 0.0)
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            return b""
        try:
            return os.read(fd, 65536)
        except (BlockingIOError, InterruptedError, OSError):
            return b""

    def flush(self) -> None:
        """Discard any pending input."""
        while self._read_chunk(0.0):
            pass
        self.parser._buf.clear()  # noqa: SLF001


#: Convenience predicate for the common "quit" keys.
def is_quit(event: Event, keys: tuple[str, ...] = ("q", "ctrl-c", "escape")) -> bool:
    return isinstance(event, Key) and event.name in keys


__all__ = [
    "Event",
    "FocusEvent",
    "Key",
    "Keyboard",
    "MouseEvent",
    "Parser",
    "PasteEvent",
    "ResizeEvent",
    "is_quit",
]

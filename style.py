"""Colours and text styles.

A :class:`Style` is an immutable, hashable description of *how* a cell looks:
foreground, background, the eight classic attributes, underline decoration,
underline colour and an OSC-8 hyperlink. Because styles are hashable they can
be shared between millions of cells and compared with ``==`` in the diff loop.

Every colour can be expressed as a name, an 8/16 ANSI index, a 256-palette
index or a true-colour RGB triple, and is automatically down-sampled to
whatever the terminal actually supports (:class:`ColorDepth`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum

from . import ansi

# --------------------------------------------------------------------------
# Colour depth
# --------------------------------------------------------------------------


class ColorDepth(Enum):
    """How many colours the output is allowed to use."""

    AUTO = "auto"
    NONE = "none"  # strip all colour
    BASIC = "basic"  # 8 / 16 ANSI colours
    EXTENDED = "extended"  # 256 colour palette
    TRUECOLOR = "truecolor"  # 24-bit RGB

    @classmethod
    def detect(cls, env: dict[str, str] | None = None, term: str | None = None) -> ColorDepth:
        """Sniff the terminal's colour support from the environment."""
        import os

        env = env if env is not None else os.environ
        term = term if term is not None else env.get("TERM", "")
        colorterm = env.get("COLORTERM", "").lower()

        if term in ("dumb", "") and not colorterm:
            return cls.NONE
        if colorterm in ("truecolor", "24bit"):
            return cls.TRUECOLOR
        if env.get("NO_COLOR"):  # https://no-color.org
            return cls.NONE
        if "256" in term or env.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm"):
            return cls.EXTENDED
        if term in ("linux",):
            return cls.BASIC
        return cls.EXTENDED if term else cls.BASIC


#: Standard xterm palette for the first 16 slots.
_BASE_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
    (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
    (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
)


def _palette() -> tuple[tuple[int, int, int], ...]:
    """The full 256-entry xterm palette (built once, cached)."""
    global _PALETTE
    if _PALETTE:
        return _PALETTE
    pal = list(_BASE_PALETTE)
    levels = (0, 95, 135, 175, 215, 255)
    for r in levels:
        for g in levels:
            for b in levels:
                pal.append((r, g, b))
    for i in range(24):
        v = 8 + i * 10
        pal.append((v, v, v))
    _PALETTE = tuple(pal)
    return _PALETTE


_PALETTE: tuple[tuple[int, int, int], ...] = ()


#: Named colours accepted by :meth:`Color.parse`.
NAMED_COLORS: dict[str, int] = {
    "black": 0, "red": 1, "green": 2, "yellow": 3, "blue": 4,
    "magenta": 5, "cyan": 6, "white": 7, "gray": 8, "grey": 8,
    "bright-black": 8, "bright-red": 9, "bright-green": 10,
    "bright-yellow": 11, "bright-blue": 12, "bright-magenta": 13,
    "bright-cyan": 14, "bright-white": 15,
}


def _rgb_to_256(r: int, g: int, b: int) -> int:
    """Nearest match in the 256 colour palette (Euclidean distance in RGB)."""
    if r == g == b:  # greyscale ramp is a better fit for neutrals
        if r < 8:
            return 16
        if r > 248:
            return 231
        return 232 + round((r - 8) / 247 * 23)
    levels = (0, 95, 135, 175, 215, 255)

    def nearest(v: int) -> int:
        return min(range(6), key=lambda i: abs(levels[i] - v))

    return 16 + 36 * nearest(r) + 6 * nearest(g) + nearest(b)


def _rgb_to_basic(r: int, g: int, b: int) -> int:
    """Nearest match among the 16 ANSI colours."""
    best, best_d = 7, None
    for idx, (pr, pg, pb) in enumerate(_BASE_PALETTE):
        d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
        if best_d is None or d < best_d:
            best, best_d = idx, d
    return best


@dataclass(frozen=True, slots=True)
class Color:
    """A colour in one of four encodings.

    ``kind``      ``value``
    ------------  -----------------------------------------------
    ``default``   ignored (terminal default)
    ``ansi``      0-15
    ``256``       0-255
    ``rgb``       ``(r, g, b)`` ints 0-255
    """

    kind: str = "default"
    value: object = None

    # -- constructors ----------------------------------------------------
    @classmethod
    def default(cls) -> Color:
        return cls("default", None)

    @classmethod
    def ansi(cls_, index: int) -> Color:
        index = int(index) % 16
        return cls_("ansi", index)

    @classmethod
    def c256(cls_, index: int) -> Color:
        index = int(index) % 256
        if index < 16:
            return cls_("ansi", index)
        return cls_("256", index)

    @classmethod
    def rgb(cls_, r: int, g: int, b: int) -> Color:
        return cls_("rgb", (max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b)))))

    @classmethod
    def hex(cls_, text: str) -> Color:
        text = text.strip().lstrip("#")
        if len(text) == 3:
            text = "".join(c * 2 for c in text)
        if len(text) == 6:
            return cls_.rgb(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
        raise ValueError(f"invalid hex colour: {text!r}")

    @classmethod
    def hsl(cls_, h: float, s: float, l: float) -> Color:  # noqa: E741
        """HSL with ``h`` in degrees, ``s``/``l`` in 0..1."""
        import colorsys

        r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
        return cls_.rgb(round(r * 255), round(g * 255), round(b * 255))

    @classmethod
    def hsv(cls_, h: float, s: float, v: float) -> Color:
        """HSV with ``h`` in degrees, ``s``/``v`` in 0..1."""
        import colorsys

        r, g, b = colorsys.hsv_to_rgb((h % 360) / 360.0, s, v)
        return cls_.rgb(round(r * 255), round(g * 255), round(b * 255))

    @classmethod
    def lerp(cls_, a: Color, b: Color, t: float) -> Color:
        """Linear blend between two colours, ``t`` in 0..1."""
        ar, ag, ab = a.to_rgb()
        br, bg, bb = b.to_rgb()
        t = max(0.0, min(1.0, t))
        return cls_.rgb(round(ar + (br - ar) * t), round(ag + (bg - ag) * t), round(ab + (bb - ab) * t))

    @classmethod
    def parse(cls_, spec: ColorLike) -> Color:
        """Build a colour from almost anything sensible.

        Accepts :class:`Color`, ``None``/``"default"``, a name (``"red"``),
        a hex string (``"#ff8800"``, ``"f80"``), ``"rgb(1,2,3)"``,
        ``"256:208"``, a palette int (0-255) or an ``(r, g, b)`` tuple.
        """
        if spec is None:
            return cls_.default()
        if isinstance(spec, Color):
            return spec
        if isinstance(spec, bool):
            raise TypeError("colour must not be a bool")
        if isinstance(spec, int):
            return cls_.c256(spec)
        if isinstance(spec, (tuple, list)):
            if len(spec) == 3:
                return cls_.rgb(*spec)
            if len(spec) == 1:
                return cls_.c256(spec[0])
            raise ValueError(f"cannot parse colour sequence {spec!r}")
        if not isinstance(spec, str):
            raise TypeError(f"cannot parse colour of type {type(spec).__name__}")

        text = spec.strip()
        low = text.lower()
        if low in ("", "default", "none", "reset"):
            return cls_.default()
        if low in NAMED_COLORS:
            return cls_.ansi(NAMED_COLORS[low])
        if low.startswith("rgb(") and low.endswith(")"):
            parts = [p.strip() for p in low[4:-1].split(",")]
            return cls_.rgb(*(int(float(p)) for p in parts))
        if low.startswith(("256:", "p:", "palette:")):
            return cls_.c256(int(low.split(":", 1)[1]))
        if low.startswith(("ansi:", "a:")):
            return cls_.ansi(int(low.split(":", 1)[1]))
        if low.startswith("#") or (len(low) in (3, 6) and all(c in "0123456789abcdef" for c in low)):
            return cls_.hex(low)
        raise ValueError(f"unknown colour {spec!r}")

    # -- queries ---------------------------------------------------------
    @property
    def is_default(self) -> bool:
        return self.kind == "default"

    def to_rgb(self) -> tuple[int, int, int]:
        """Best-effort RGB equivalent (defaults resolve to a mid grey)."""
        if self.kind == "rgb":
            return self.value  # type: ignore[return-value]
        if self.kind == "256":
            return _palette()[self.value]  # type: ignore[index]
        if self.kind == "ansi":
            return _BASE_PALETTE[self.value]  # type: ignore[index]
        return (192, 192, 192)

    def luminance(self) -> float:
        """Perceived brightness 0..1 (handy for auto contrast)."""
        r, g, b = self.to_rgb()
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0

    def contrast(self, other: Color) -> Color:
        """Black or white, whichever reads better on ``other``."""
        return Color.rgb(0, 0, 0) if other.luminance() > 0.55 else Color.rgb(255, 255, 255)

    def downsample(self, depth: ColorDepth) -> Color:
        """Convert to the richest encoding allowed by ``depth``."""
        if self.is_default or depth in (ColorDepth.TRUECOLOR, ColorDepth.AUTO):
            return self
        if depth is ColorDepth.NONE:
            return Color.default()
        if depth is ColorDepth.EXTENDED and self.kind == "rgb":
            return Color.c256(_rgb_to_256(*self.to_rgb()))
        if depth is ColorDepth.BASIC:
            if self.kind == "rgb":
                return Color.ansi(_rgb_to_basic(*self.to_rgb()))
            if self.kind == "256":
                return Color.ansi(_rgb_to_basic(*self.to_rgb()))
        return self

    # -- output ----------------------------------------------------------
    def sgr(self, foreground: bool = True, depth: ColorDepth = ColorDepth.TRUECOLOR) -> list[str]:
        """SGR parameters for this colour (empty list when default)."""
        colour = self.downsample(depth)
        base = (30, 40)[not foreground]
        hi = (90, 100)[not foreground]
        ext = (38, 48)[not foreground]

        if colour.kind == "default":
            return []
        if colour.kind == "ansi":
            index = colour.value
            return [str(base + index if index < 8 else hi + index - 8)]  # type: ignore[operator]
        if colour.kind == "256":
            return [f"{ext};5;{colour.value}"]
        r, g, b = colour.value  # type: ignore[misc]
        return [f"{ext};2;{r};{g};{b}"]

    def __str__(self) -> str:
        if self.kind == "default":
            return "default"
        if self.kind == "rgb":
            r, g, b = self.value  # type: ignore[misc]
            return f"#{r:02x}{g:02x}{b:02x}"
        return f"{self.kind}:{self.value}"


#: Anything :meth:`Color.parse` accepts.
ColorLike = "Color | str | int | tuple[int, int, int] | None"


# --------------------------------------------------------------------------
# Attributes
# --------------------------------------------------------------------------


class Underline(Enum):
    """Underline decoration (SGR 4:<n>)."""

    NONE = 0
    SINGLE = 1
    DOUBLE = 2
    CURLY = 3
    DOTTED = 4
    DASHED = 5


_UNDERLINE_ALIASES = {
    "": Underline.SINGLE,
    "none": Underline.NONE,
    "off": Underline.NONE,
    "false": Underline.NONE,
    "single": Underline.SINGLE,
    "true": Underline.SINGLE,
    "on": Underline.SINGLE,
    "double": Underline.DOUBLE,
    "curly": Underline.CURLY,
    "wavy": Underline.CURLY,
    "wave": Underline.CURLY,
    "dotted": Underline.DOTTED,
    "dot": Underline.DOTTED,
    "dashed": Underline.DASHED,
    "dash": Underline.DASHED,
}
_UNDERLINE_ALIASES.update({u.name.lower(): u for u in Underline})


def parse_underline(value: object) -> Underline:
    """Accept ``True/False``, an :class:`Underline`, or a friendly name."""
    if isinstance(value, Underline):
        return value
    if isinstance(value, bool):
        return Underline.SINGLE if value else Underline.NONE
    if isinstance(value, int):
        try:
            return Underline(value)
        except ValueError:
            raise ValueError(f"unknown underline code {value!r}") from None
    try:
        return _UNDERLINE_ALIASES[str(value).strip().lower()]
    except KeyError:
        raise ValueError(
            f"unknown underline {value!r}; choose from "
            f"{sorted(set(_UNDERLINE_ALIASES) - {'', 'true', 'false', 'on', 'off'})}"
        ) from None


#: ``(field name, on-code, off-code)`` for every boolean attribute.
_ATTRS: tuple[tuple[str, int, int], ...] = (
    ("bold", 1, 22),
    ("dim", 2, 22),
    ("italic", 3, 23),
    ("blink", 5, 25),
    ("reverse", 7, 27),
    ("hidden", 8, 28),
    ("strike", 9, 29),
)


def _underline_color_sgr(colour: Color, depth: ColorDepth) -> str:
    """SGR 58 parameters for an underline colour."""
    if colour.is_default:
        return ""
    colour = colour.downsample(depth)
    if colour.kind == "rgb":
        r, g, b = colour.value  # type: ignore[misc]
        return f"58;2;{r};{g};{b}"
    return f"58;5;{colour.value}"


@dataclass(frozen=True, slots=True)
class Style:
    """Immutable appearance of a cell or a run of text.

    All fields are optional and default to "terminal default", so
    ``Style()`` is a no-op style that costs zero escape codes.
    """

    fg: Color = Color("default", None)
    bg: Color = Color("default", None)
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: Underline | bool = Underline.NONE
    blink: bool = False
    reverse: bool = False
    hidden: bool = False
    strike: bool = False
    underline_color: Color = Color("default", None)
    link: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fg", Color.parse(self.fg))
        object.__setattr__(self, "bg", Color.parse(self.bg))
        object.__setattr__(self, "underline_color", Color.parse(self.underline_color))
        if not isinstance(self.underline, Underline):
            object.__setattr__(self, "underline", parse_underline(self.underline))

    # -- construction ----------------------------------------------------
    @classmethod
    def parse(cls, **kwargs: object) -> Style:
        """``Style.parse(fg="red", bold=True, underline="curly")``."""
        return cls(**kwargs)  # type: ignore[arg-type]

    def merge(self, other: Style | None) -> Style:
        """Layer ``other`` on top of ``self``; unset fields fall through.

        This is what makes inherited / cascading styles work: a layer style
        plus a per-object style plus a per-run style.
        """
        if other is None:
            return self
        changes: dict[str, object] = {}
        for field in self.__dataclass_fields__:  # type: ignore[attr-defined]
            value = getattr(other, field)
            default = getattr(_DEFAULT_STYLE, field)
            if value != default:
                changes[field] = value
        return replace(self, **changes) if changes else self

    def with_(self, **kwargs: object) -> Style:
        """Copy with fields replaced (``style.with_(bold=True)``)."""
        if "underline" in kwargs and isinstance(kwargs["underline"], (bool, str)):
            pass  # __post_init__ normalises it
        return replace(self, **kwargs)  # type: ignore[arg-type]

    def plain(self) -> Style:
        """This style with all colour removed."""
        return Style(underline=self.underline)

    # -- output ----------------------------------------------------------
    def sgr(self, depth: ColorDepth = ColorDepth.TRUECOLOR) -> str:
        """Complete escape sequence for this style (``""`` if empty)."""
        codes = self._codes(depth)
        if not codes:
            return ""
        return ansi.CSI + ";".join(codes) + "m"

    def _codes(self, depth: ColorDepth) -> list[str]:
        codes: list[str] = []
        codes += self.fg.sgr(True, depth)
        codes += self.bg.sgr(False, depth)
        for name, on, _off in _ATTRS:
            if getattr(self, name):
                codes.append(str(on))
        if self.underline is not Underline.NONE:
            codes.append("4" if self.underline is Underline.SINGLE else f"4:{self.underline.value}")
            codes.append(_underline_color_sgr(self.underline_color, depth))
        return [c for c in codes if c]

    def diff(self, previous: Style | None, depth: ColorDepth = ColorDepth.TRUECOLOR) -> str:
        """Minimal escape sequence to get from ``previous`` to ``self``.

        Only the parameters that actually changed are emitted, which keeps
        the byte count of a repaint low on busy screens.
        """
        if previous is None:
            return self.sgr(depth)
        if previous == self:
            return ""

        codes: list[str] = []
        # attributes that need turning OFF first
        for name, _on, off in _ATTRS:
            if getattr(previous, name) and not getattr(self, name):
                codes.append(str(off))
        if previous.underline is not Underline.NONE and self.underline is Underline.NONE:
            codes.append("24")

        if previous.fg != self.fg:
            if self.fg.is_default:
                codes.append("39")
            else:
                codes += self.fg.sgr(True, depth)
        if previous.bg != self.bg:
            if self.bg.is_default:
                codes.append("49")
            else:
                codes += self.bg.sgr(False, depth)
        if previous.underline != self.underline and self.underline is not Underline.NONE:
            codes.append("4" if self.underline is Underline.SINGLE else f"4:{self.underline.value}")
        if previous.underline_color != self.underline_color:
            if self.underline_color.is_default:
                codes.append("59")
            else:
                uc = self.underline_color.downsample(depth)
                if uc.kind == "rgb":
                    codes.append(f"58;2;{uc.value[0]};{uc.value[1]};{uc.value[2]}")
                else:
                    codes.append(f"58;5;{uc.value}")
        for name, on, _off in _ATTRS:
            if getattr(self, name) and not getattr(previous, name):
                codes.append(str(on))

        if not codes:
            return ""
        return ansi.CSI + ";".join(codes) + "m"

    def wrap(self, text: str, depth: ColorDepth = ColorDepth.TRUECOLOR) -> str:
        """Return ``text`` with this style applied and then reset."""
        if not text:
            return ""
        out = self.sgr(depth)
        if self.link:
            out += ansi.OSC + f"8;;{self.link}" + ansi.ST
        out += text
        if self.link:
            out += ansi.OSC + "8;;" + ansi.ST
        return out + ansi.RESET if out.startswith(ansi.CSI) or self.link else text

    def __bool__(self) -> bool:  # a style is "empty" when it is the default
        return self != _DEFAULT_STYLE

    def __str__(self) -> str:
        parts = []
        if not self.fg.is_default:
            parts.append(f"fg={self.fg}")
        if not self.bg.is_default:
            parts.append(f"bg={self.bg}")
        for name, _on, _off in _ATTRS:
            if getattr(self, name):
                parts.append(name)
        if self.underline is not Underline.NONE:
            parts.append(f"underline={self.underline.name.lower()}")
        if self.link:
            parts.append(f"link={self.link}")
        return " ".join(parts) or "default"


_DEFAULT_STYLE = Style()


def normalize_style(
    style: Style | dict | None = None,
    /,
    base: Style | None = None,
    **kwargs: object,
) -> Style:
    """Build a :class:`Style` from a style object, a dict, a colour, or kwargs.

    This is the single funnel every public API uses, which is why all of these
    are equivalent wherever a style is accepted::

        screen.put(0, 0, "x", fg="red")
        screen.put(0, 0, "x", "red")
        screen.put(0, 0, "x", Color.ansi(1))
        screen.put(0, 0, "x", {"fg": "red"})
        screen.put(0, 0, "x", Style(fg=Color.hex("#f00")))
    """
    if base is None and not kwargs and type(style) is Style:
        # Style.__post_init__ already normalised every field, so this is a
        # no-op merge — and it is by far the hottest call in the library.
        return style
    result = base if base is not None else Style()
    if isinstance(style, dict):
        result = result.merge(Style.parse(**style))
    elif isinstance(style, Color):
        result = result.merge(Style(fg=style))
    elif isinstance(style, (str, int, tuple)) :
        result = result.merge(Style(fg=Color.parse(style)))
    elif style is not None:
        result = result.merge(style)
    if kwargs:
        result = result.merge(Style.parse(**kwargs))
    return result


def style_runs(
    runs: Iterable[tuple[str, Style | None]],
    depth: ColorDepth = ColorDepth.TRUECOLOR,
) -> str:
    """Render ``[(text, style), ...]`` into one self-contained ANSI string."""
    out: list[str] = []
    current: Style | None = None
    styled = False
    for text, style in runs:
        if not text:
            continue
        style = style or Style()
        if current is None or style != current:
            out.append(style.diff(current, depth))
            current = style
            styled = styled or bool(style)
        out.append(text)
    if styled:
        out.append(ansi.RESET)
    return "".join(out)


# Convenience palette ------------------------------------------------------
DEFAULT = Style()
BOLD = Style(bold=True)
DIM = Style(dim=True)
ITALIC = Style(italic=True)
UNDERLINED = Style(underline=Underline.SINGLE)
REVERSED = Style(reverse=True)
STRUCK = Style(strike=True)


def make_presets() -> dict[str, Style]:
    """``Style`` presets for each of the 8 base + 8 bright colours."""
    presets: dict[str, Style] = {}
    for name, index in NAMED_COLORS.items():
        presets[name] = Style(fg=Color.ansi(index))
        presets[f"on_{name.replace('-', '_')}"] = Style(bg=Color.ansi(index))
    return presets


GLOBALS = make_presets()


def presets() -> dict[str, Style]:
    return dict(GLOBALS)


__all__ = [
    "BOLD",
    "Color",
    "ColorDepth",
    "ColorLike",
    "DEFAULT",
    "DIM",
    "ITALIC",
    "NAMED_COLORS",
    "REVERSED",
    "STRUCK",
    "Style",
    "UNDERLINED",
    "Underline",
    "normalize_style",
    "parse_underline",
    "presets",
    "style_runs",
]

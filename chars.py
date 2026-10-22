"""Box-drawing character sets and the primitives that use them."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BoxChars:
    """The nine glyphs used to draw a rectangle."""

    horizontal: str = "─"
    vertical: str = "│"
    top_left: str = "╭"
    top_right: str = "╮"
    bottom_left: str = "╰"
    bottom_right: str = "╯"
    tee_down: str = "┬"  # ┬
    tee_up: str = "┴"  # ┴
    tee_right: str = "├"  # ├
    tee_left: str = "┤"  # ┤
    cross: str = "┼"  # ┼

    def corners(self) -> tuple[str, str, str, str]:
        return self.top_left, self.top_right, self.bottom_left, self.bottom_right


#: Rounded corners — the default.
ROUNDED = BoxChars()

#: Sharp corners.
SHARP = BoxChars("─", "│", "┌", "┐", "└", "┘", "┬", "┴", "├", "┤", "┼")

#: Double lines.
DOUBLE = BoxChars("═", "║", "╔", "╗", "╚", "╝", "╦", "╩", "╠", "╣", "╬")

#: Heavy / bold lines.
HEAVY = BoxChars("━", "┃", "┏", "┓", "┗", "┛", "┳", "┻", "┣", "┫", "╋")

#: Plain ASCII fallback for terminals without Unicode support.
ASCII = BoxChars("-", "|", "+", "+", "+", "+", "+", "+", "+", "+", "+")

#: Block characters, useful for bars, heatmaps and pixel art.
BLOCKS = (" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█")
V_BLOCKS = ("▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")
SHADES = (" ", "░", "▒", "▓", "█")
BRAILLE_BASE = 0x2800
DOTS = "⠁⠂⠄⡀⠈⠐⠠⢀"  # bit order for braille pixel drawing

#: Progress bar / sparkline glyphs.
BARS = ("🌑", "🌒", "🌓", "🌔", "🌕")
SPARKS = ("▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")

BOX_SETS = {
    "rounded": ROUNDED,
    "sharp": SHARP,
    "double": DOUBLE,
    "heavy": HEAVY,
    "ascii": ASCII,
}


def box_set(name: str | BoxChars | None) -> BoxChars:
    """Resolve ``"rounded" | "sharp" | "double" | "heavy" | "ascii"``."""
    if name is None:
        return ROUNDED
    if isinstance(name, BoxChars):
        return name
    try:
        return BOX_SETS[name.lower()]
    except KeyError:
        raise ValueError(f"unknown box set {name!r}; choose from {sorted(BOX_SETS)}") from None


__all__ = [
    "ASCII",
    "BLOCKS",
    "BOX_SETS",
    "BoxChars",
    "DOUBLE",
    "HEAVY",
    "ROUNDED",
    "SHADES",
    "SHARP",
    "SPARKS",
    "V_BLOCKS",
    "box_set",
]

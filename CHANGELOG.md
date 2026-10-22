# Changelog

All notable changes to `termrender` are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

### Added
- **`tred`**, a text editor built on the public API only — 2 931 lines across
  `document.py` (text + undo), `syntax.py` (highlighting), `view.py` (layout +
  overlays) and `app.py` (bindings + commands). Run it with `python -m tred
  FILE`. Six syntaxes plus a plain fallback, two themes, 74 bindings over 63
  commands, OSC 52 clipboard, mouse selection, drag and wheel scrolling.
- `Screen.paint_runs()` and `Layer.paint_runs()`: a line of mixed-colour text
  in one call, without an object handle — for content rewritten every frame,
  where handles would be pure overhead.
- `ansi.clipboard()` / `ansi.query_clipboard()` (OSC 52), plus
  `Screen.copy_to_clipboard()` and `Screen.send()` for raw escape output.
- `HOW_IT_WORKS.html`: an illustrated, fully self-contained explanation of the
  library — every module, the pipeline stage by stage, colour handling, six
  worked examples and real frames captured headlessly while the page was built.

### Fixed
- `Buffer.ansi()` — and therefore `Screen.frame_ansi()` and `screenshot()` —
  skipped untouched cells instead of emitting them as spaces, so coloured
  output placed text in different columns than `frame()` and `row_text()` did.
  A gap of never-written cells simply vanished.
- `rstrip=True` in `Buffer.ansi()` now drops trailing blank *cells* before the
  escapes are built. It used to rstrip the finished string, where those spaces
  were already wrapped in colour codes and invisible to `str.rstrip()`.
- The input parser no longer reads `shift+f3` (`CSI 1;2 R`) as a cursor
  position report; a leading `1;` is what distinguishes modified function keys
  from `CSI row;col R`.
- Mouse decoding splits the action from the button: bit 64 is checked first so
  wheel events are never reported as presses, bit 32 means drag (or motion when
  no button is held), and the `M`/`m` final byte means press/release. Modifiers
  are carried in the action name (`press+shift`).

### Quality
- 507 tests (373 engine + 134 editor), all headless; `ruff check .` clean.

---

## 1.0.0 — 2026-10-18

First release.

### The engine
- `Screen`: layer compositing, front/back buffer diffing, run-grouped output,
  minimal SGR deltas, `clear-to-EOL` collapsing and synchronized-update
  framing.
- `Buffer`: a standalone cell grid with text placement, wide-character
  handling, rectangles, lines, fills, blits, scrolling, resizing, search and
  bounding boxes.
- `Layer`: z-ordered surfaces with an offset, visibility, transparency and
  their own object registry. Assigning `z`, `visible`, `offset`,
  `transparent` or `clip` invalidates the composite automatically.
- `TextObject`: text that knows which cells it owns — move, shift, centre,
  restyle, append, wrap, align, hide, remove.
- `Match`: the result of locating text, for objects and raw cells alike.

### Text handling
- Put, locate, move and remove text by `(x, y)`, by id or by handle.
- Double-width CJK/emoji occupy two cells with a continuation marker, so
  overwriting and erasing never leave half a glyph behind.
- Zero-width combining marks fold into the preceding cell.
- Word-aware wrapping with hard-split fallback, per-cell alignment, tabs and
  multi-line strings.

### Colour and style
- `Color` with named / ANSI / 256 / RGB / HSL / HSV encodings, hex parsing,
  blending, luminance and automatic contrast.
- `ColorDepth` auto-detection from `COLORTERM`, `TERM` and `NO_COLOR`, with
  down-sampling to 256 and 16 colours.
- `Style` covering foreground, background, bold, dim, italic, five underline
  decorations, underline colour, blink, reverse, hidden, strike and OSC-8
  hyperlinks. Frozen, hashable, shareable.

### Input
- Incremental byte parser producing `Key`, `MouseEvent`, `PasteEvent`,
  `FocusEvent` and `ResizeEvent`, with UTF-8 reassembly across reads.
- Canonical key names, modifier decoding, SGR mouse, bracketed paste and
  cursor-position report filtering.
- `Keyboard` raw-mode reader that degrades to a no-op when stdin is not a tty.

### Loop and widgets
- `App` with `start` / `stop` / `tick` / `key` / `mouse` / `resize` / `focus` /
  `paste` / `event` hooks, per-key bindings, frame-rate cap and `step()` for
  driving frames yourself.
- Widgets: `panel`, `bar` (partial blocks, gradients, labels), `sparkline`,
  `heatmap`, `table`, `menu`, `text_block`, `key_hints`, `gradient_text`.

### Configuration
- `Config` exposes every knob: geometry and clamping, resize following,
  alternate screen, cursor visibility/position/shape, mouse, focus events,
  bracketed paste, application keys, line wrap, window title, scrollback,
  colour depth, default style, background character and style, diffing,
  synchronized output, write batching, flushing, Unicode fallback, row
  trimming, frame-rate cap, input timeout, output stream, quiet mode,
  exit restoration and stats.
- `Stats` with frames, fps, cells changed, bytes written and draw/composite
  timings.

### Quality
- 368 tests covering styles, widths, buffers, layers, objects, the engine's
  byte output, terminal modes, input parsing, widgets and the app loop.
- Headless rendering (`quiet=True`, `frame()`, `frame_ansi()`, `render()`) for
  tests, docs and captures.
- Five runnable examples, `ruff` clean, no runtime dependencies.

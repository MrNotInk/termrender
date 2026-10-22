# termrender — architecture

This document explains *how the engine works and why it is shaped the way it
is*. For usage, see [README.md](README.md).

---

## 1. The mental model

A terminal is not a stream of text. It is a **grid of cells** — `columns ×
rows` — and each cell holds exactly one character plus one set of graphic
attributes. The cursor is a write head that auto-advances. Escape sequences are
the only way to talk to it:

| Sequence | Meaning |
|---|---|
| `ESC [ y ; x H` | move the write head to a cell (1-based!) |
| `ESC [ 31 m` | set foreground red |
| `ESC [ 0 K` | clear from cursor to end of line |
| `ESC [ ?25l` | hide the cursor |
| `ESC [ ?2026h` | begin a synchronized update |

`termrender` keeps its own model of that grid in memory, lets you mutate the
model with ordinary Python calls, and then works out the **shortest byte
sequence** that turns what the terminal currently shows into what your model
says. That last step is the whole game — everything else exists to support it.

This is **immediate-mode rendering with a retained-object convenience layer**:
you describe the frame, the engine diffs it. But because "remove this text" is
impossible in pure immediate mode without remembering what you drew, `put()`
hands you a `TextObject` that remembers its own cells.

---

## 2. The frame pipeline

```
 your code
     │  screen.put(x, y, "hi", fg="red", id="k")
     ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Layer stack (sorted by z)                                   │
 │   base   ├ paint buffer   ← raw cells (paint/fill/rect/line) │
 │          └ object buffer  ← baked from the object registry   │
 │   hud    ├ paint buffer                                     │
 │          └ object buffer                                    │
 └─────────────────────────────────────────────────────────────┘
     │  composite: back.clear(background) then blit each layer
     ▼
 back buffer  ──────diff──────▶  changed cells (indices)
     │                              │
     │                              ▼  group into horizontal runs
     │                          per run: one CUP + style deltas + glyphs
     │                              │
     │                              ▼  trailing blanks → ESC[0K at row end
     │                          payload string (+ sync-update wrapper)
     │                              │
     ▼                              ▼  write / flush
 front buffer ◀──── swap ────────  terminal
```

Stage by stage:

**1. Draw calls.** Every mutation lands in a `Layer`. Objects go into the
layer's registry; raw drawing goes straight into its `paint` buffer. Each
mutation sets a dirty flag that propagates to the `Screen` through a `notify`
callback.

**2. Composite** (`Screen._composite_into`). The back buffer is reset to the
configured background, then each visible layer is blitted bottom-to-top. A
layer contributes its `paint` buffer first, then its baked objects on top,
both at the layer's `offset`. Blitting is *transparent*: cells holding the
`EMPTY` sentinel are skipped, so a layer only covers what it actually paints.

**3. Bake** (`Layer.bake`). Objects are not stored as cells; they are
rasterised on demand. If nothing in the registry changed since the last frame,
the cached object buffer is reused — so a screen with 500 static objects and
one moving one only re-rasterises when the registry is dirty.

**4. Diff** (`Screen._paint_diff`). One pass over `zip(back, front)`
collects `(index, cell)` pairs where the cells differ. The comparison has a
fast path: cells are *interned*, so `b is f` usually short-circuits before the
more expensive `b == f`.

**5. Run grouping.** Consecutive changed indices on the same row become one
run. Each run costs a single cursor move instead of one per cell.

**6. Byte generation.** For each cell in a run: emit the *difference* between
the style the terminal currently has and the style this cell wants, then emit
the character. Continuation cells (the right half of a wide glyph) are skipped,
because printing the wide glyph already advanced the cursor two columns.

**7. Swap.** `front, back = back, front`. The buffer we just composited
becomes the record of what is on screen; the old front becomes next frame's
scratch buffer. No copying.

If nothing is dirty, `draw()` returns immediately without compositing — an
idle screen costs one function call per frame.

---

## 3. The data model

### `Cell` — one character + one style
A frozen, slotted dataclass. Frozen means hashable and shareable; slotted means
no per-instance `__dict__`. Three singletons carry special meaning:

| Sentinel | `char` | Meaning |
|---|---|---|
| `EMPTY_CELL` | `\x00` | paints nothing — layers below show through |
| `BLANK_CELL` | `" "` | paints a space — opaque |
| `CONT_CELL` | `\x01` | right half of a double-width glyph |

The distinction between `EMPTY` and `" "` is what makes layering, erasing and
byte-saving all work at once.

`cell(char, style)` interns instances in a bounded dict. Because the same
`(char, style)` pair returns the *same object*, the diff loop's identity check
succeeds for the vast majority of cells, and equal cells never allocate.

### `Buffer` — the grid
A flat `list[Cell]` of length `w × h`, indexed `y * w + x`. Flat beats nested
lists for cache locality and makes row slicing (`cells[a:b]`) a single C-level
operation, which `blit` and `scroll` exploit.

Public surface: `put`, `put_runs`, `erase`, `blank`, `fill`, `clear`, `rect`,
`hline`, `vline`, `line` (Bresenham), `blit`, `scroll`, `resize`, `copy`,
`find`, `run_bounds`, `bbox`, `text`, `ansi`, `rows`.

A `Buffer` is a complete render target on its own — you never need a `Screen`
to use one, which is why the headless path and the widget tests are trivial.

### `Layer` — a surface with identity
Two buffers (raw `paint` + baked objects) plus an object registry, plus `z`,
`offset`, `visible`, `transparent`, `clip`. Those five are **properties**:
assigning any of them calls `mark_dirty()`, which notifies the screen. That is
why `hud.visible = False` just works — the engine found out.

### `TextObject` — text that knows where it lives
Stores `x`, `y`, `id`, `visible`, `wrap`, `align`, `z` and a list of *runs*
(`[(text, style), …]`) rather than a plain string, so one object can hold
mixed styling. Layout is computed lazily by `_layout()`, which calls
`split_runs()` to fold the run list into lines.

### `Style` / `Color` — appearance
`Style` is frozen and hashable, with `Color` values for `fg`, `bg` and
`underline_color`, booleans for the classic attributes, an `Underline` enum for
the five decoration types, and an optional OSC-8 `link`.

`Color` has four encodings (`default`, `ansi`, `256`, `rgb`) and one
`downsample(depth)` method that collapses them towards whatever the terminal
supports. `Color.parse` is the universal front door: it accepts names, hex,
`"rgb(…)"`, `"256:208"`, ints and tuples.

`normalize_style(style, /, base=None, **kwargs)` is the single funnel every
public API calls, which is why all five of these are interchangeable:

```python
screen.put(0, 0, "x", fg="red")
screen.put(0, 0, "x", "red")
screen.put(0, 0, "x", Color.ansi(1))
screen.put(0, 0, "x", {"fg": "red"})
screen.put(0, 0, "x", Style(fg=Color.hex("#f00")))
```

---

## 4. Module map

| Module | Lines | Responsibility |
|---|---|---|
| `ansi.py` | ~150 | Escape-sequence constants and builders; `strip_ansi` |
| `text.py` | ~175 | Character widths, truncation, padding, wrapping, cell slicing |
| `style.py` | ~640 | `Color`, `Style`, `ColorDepth`, `Underline`, SGR generation and diffing |
| `cell.py` | ~75 | `Cell` + interning |
| `chars.py` | ~90 | Box-drawing sets, block ramps, sparkline glyphs |
| `buffer.py` | ~640 | The grid and every drawing primitive |
| `objects.py` | ~350 | `TextObject` and the word-wrapping run splitter |
| `layer.py` | ~410 | Layers: registry, baking, composition |
| `config.py` | ~300 | `Config`, `Stats`, terminal detection |
| `screen.py` | ~950 | The engine: lifecycle, modes, compositing, diffing, I/O, locating |
| `input.py` | ~475 | Byte stream → typed events |
| `app.py` | ~215 | Event loop, hooks, frame pacing |
| `widgets.py` | ~380 | Panels, bars, sparklines, tables, menus, hints |

Dependencies point one way: `ansi`/`text`/`style` → `cell` → `buffer` →
`objects` → `layer` → `screen` → `app`. `widgets` sits beside `screen` and
only uses the shared duck-typed drawing surface (`put`, `put_runs`, `fill`,
`rect`, `hline`), which is why every widget accepts either a `Screen` or a
`Layer`.

---

## 5. The hard problems

### 5.1 Characters are not cells
`char_width()` classifies each character via `unicodedata`:

- combining marks (`Mn`, `Me`), format chars (`Cf`), control/surrogate/
  private-use → **0 cells**
- `east_asian_width` in `("W", "F")` → **2 cells**
- everything else → **1 cell**

An ASCII fast path (`text.isascii() → len(text)`) keeps the common case free.

A double-width glyph occupies two cells, so the second cell stores a
`CONTINUATION` marker. That creates four corruption hazards, all handled in
`Buffer._stamp`:

1. Writing a **narrow char onto the right half** of a wide glyph orphans the
   left half → clear cell `x-1`.
2. Writing a **narrow char onto the left half** orphans the right half → clear
   cell `x+1`.
3. Writing a **wide char over a wide char's right half** → clear both `x-1`
   and `x+2`.
4. **Rendering** must not emit continuation cells as characters — the wide
   glyph already advanced the cursor by two. The diff renderer skips them, and
   if a run *starts* on a continuation cell it backs up one index so the pair
   is emitted together.

Erasing has the same hazards: `erase()` collapses a wide glyph whose
continuation cell falls inside the rectangle, and clears a continuation cell
just past the rectangle's right edge.

Zero-width combining marks are folded into the previous cell's character
string (`"e" + "\u0301"`), so an accented letter stays one cell wide and still
round-trips through `text_at`.

### 5.2 Removing text
Three different meanings, three APIs:

| Call | Removes |
|---|---|
| `remove(id)` / `remove(obj)` | the registered object, whole |
| `remove((x, y))` / `remove(x, y)` | the object covering that cell; else the raw run |
| `erase(x, y, w, h)` | an explicit rectangle of raw cells |

For raw cells, `Buffer.run_bounds(x, y)` grows left and right until a blank
cell (stepping over continuation markers), so erasing "one character of a
word" erases the word — which is exactly what `text_at(x, y)` reports. The two
agree by construction, and there is a test asserting that invariant.

Removed cells become `EMPTY`, not spaces. That matters: on a layered screen an
erased cell must let the layer below show through, and on the bottom layer it
costs zero bytes because `EMPTY` is trimmable.

### 5.3 Style state is sticky
SGR attributes persist until changed. If frame 1 paints red bold text and
frame 2 erases it with plain spaces, those spaces inherit red-bold — invisible
for a space, catastrophic for a background colour.

The renderer therefore tracks `self._term_style`: the style the terminal is
*currently* in, carried **across frames**. Each cell emits
`cell.style.diff(term_style)` — only the parameters that differ. Turning
attributes off uses their dedicated codes (`22` bold/dim, `23` italic, `24`
underline, `25` blink, `27` reverse, `28` hidden, `29` strike, `39`/`49`
default fg/bg, `59` default underline colour) rather than a blanket `ESC[0m`.

`_term_style` is reset to `None` on `force_repaint()` and on terminal
setup/teardown, where the terminal is known to be in a default state.

### 5.4 Blank does not always mean skippable
A space with a background colour, an underline or a reverse attribute is
*visible*. `CLEAR_TO_EOL` would destroy it. So `_trimmable()` is deliberately
strict:

```python
cell.char in (" ", EMPTY) and not cell.style     # Style.__bool__ is False only for the default
```

And trailing-blank collapsing only happens when the run reaches the **last
column of the row** — otherwise the unchanged cells to the right would be
wiped. Mid-row gaps are painted with real spaces. Both behaviours are pinned by
tests.

### 5.5 Tearing
Terminals scan out as bytes arrive, so a frame that paints the top before the
bottom shows a split image. `ESC[?2026h` … `ESC[?2026l` asks the terminal to
buffer the update and present it atomically. `sync_output=True` by default;
terminals that do not implement it ignore the sequence harmlessly.

### 5.6 Resizing
`SIGWINCH` sets `_pending_resize`; the next `poll()` applies it. Resizing
rebuilds both buffers, resizes every layer (content anchored top-left, other
anchors available on `Buffer.resize`), and forces a full repaint — because the
terminal reflowed underneath us and our model of it is now wrong.

### 5.7 Input is ambiguous by design
`ESC` alone means "the Escape key", but it is also the first byte of every
arrow key. The parser resolves this by checking whether more bytes are already
buffered; `Keyboard.read()` additionally re-reads with a short
`esc_timeout` (20 ms) when a lone escape arrived and the buffer is mid-sequence.

Three more subtleties:

- **UTF-8 splitting.** A multi-byte character can strangle two `read()` calls.
  `Parser.feed_bytes` runs through `codecs.getincrementaldecoder("utf-8")`, so
  a partial character is buffered, not replaced with ``.
- **Incomplete CSI.** `ESC [` with nothing after it must not be flushed as
  garbage; `_csi` returns "consumed 0" and waits.
- **Bracketed paste.** Between `ESC[200~` and `ESC[201~` the parser switches
  to a literal mode where arrow-key bytes are *data*, not events.

`space` is given a bindable name (`Key("space", " ")`) because `@app.on_key(" ")`
is a bug waiting to happen.

### 5.8 Giving the terminal back
`start()` records and applies modes; `stop()` reverses them in the opposite
order, restores the cursor shape, shows the cursor, re-enables line wrap, and
exits the alternate screen. `stop()` is registered with `atexit` and is
idempotent. `Keyboard` saves and restores the `termios` attributes.

Three graceful-degradation rules keep the library usable everywhere:

1. If the output stream is not a tty, mode sequences are skipped but frames
   still render — so piping ANSI into a file works.
2. If stdin is not a tty, `Keyboard` becomes a no-op reader instead of raising.
3. `quiet=True` runs the entire pipeline in memory and writes nothing at all.

Writes are wrapped in `except (ValueError, OSError)` so a closed stream or a
broken pipe (your viewer hit `q`) cannot take the app down mid-frame.

---

## 6. Performance engineering

Measured: 80×24 with one moving label = **0.7 ms/frame, 33 bytes**; 200×60 =
**4.2 ms/frame, 37 bytes**; full-screen churn of 12 000 cells = **18 ms**.

What buys that:

1. **Cell interning** → the diff's `b is f` identity check succeeds for most
   cells without touching `__eq__`.
2. **One `zip` comprehension** collects changed cells; no per-cell function
   calls in the scan.
3. **Run grouping** → one `ESC[y;xH` per run, not per cell.
4. **Style deltas** → typically 3–6 bytes instead of a full SGR rebuild.
5. **Row-tail collapsing** → a 200-column clear costs 4 bytes (`ESC[0K`).
6. **Slice assignment in `blit`** → aligned full-width rows copy with
   `dst[a:b] = …`, a single C-level memcpy-ish operation; the transparent case
   is one list comprehension over `zip`.
7. **Lazy object baking** → static objects are rasterised once.
8. **Dirty-flag short circuit** → an unchanged frame composites nothing.
9. **Single string join + single `write`** → one syscall per frame, unless
   `batch_writes` asks for chunking.
10. **Buffer swap instead of copy** → zero allocation per frame after warmup.

The deliberate non-goal is a C extension. Pure Python at 60 fps on a 200×60
grid leaves enough headroom for real UIs, and the package stays dependency-free
and installable anywhere.

---

## 7. Design tradeoffs

**Immediate mode + thin retained layer.** Pure immediate mode cannot answer
"remove the text at (4, 2)". Pure retained mode forces every pixel through an
object. The hybrid gives you both: `paint()` for bulk/primitive work, `put()`
when you want a handle. Objects are re-rasterised into a per-layer buffer, so
they cost the same as raw cells at composite time.

**Re-using an id replaces the object.** `screen.put(2, 0, f"hp {hp}", id="hp")`
is the single most common HUD pattern, and making it replace-in-place removes
an entire class of leak (forgetting to delete last frame's label). The cost is
that two widgets sharing a default id silently overwrite each other — which is
why every built-in widget derives its default id from its position
(`bar@{x},{y}`).

**Styles cascade by merge, not by inheritance chain.** `Style.merge(other)`
takes each field from `other` only where `other` differs from the default. That
makes `base.merge(layer_style).merge(object_style)` work, but it means you
cannot express "turn dim off" through a merge — `dim=False` is
indistinguishable from "unspecified". Where that matters (`key_hints`) the code
uses `style.with_(dim=False)`, which is `dataclasses.replace` and *is*
explicit.

**Coordinates are 0-based everywhere.** The terminal wants 1-based; the `+1`
lives in exactly one place, `ansi.move()`.

**No curses, no ncurses dependency.** curses cannot be relied on across
platforms, cannot render to a string, and hides the byte stream we need to
control. Talking ANSI directly is more code but fully portable and fully
inspectable.

**`draw()` means "present the frame"; `paint()` means "write cells".** This was
a genuine collision during development (two `draw` methods on `Screen`, the
second silently winning) and the rename is permanent. `render` and `present`
are aliases of `draw`.

---

## 8. Configuration, grouped

| Group | Knobs | Notes |
|---|---|---|
| Geometry | `width`, `height`, `min_*`, `max_*`, `follow_resize` | `None` = auto-detect; clamps apply after detection |
| Terminal modes | `alt_screen`, `hide_cursor`, `cursor`, `cursor_shape`, `mouse`, `mouse_motion`, `focus_events`, `bracketed_paste`, `application_keys`, `line_wrap`, `title`, `clear_scrollback` | `mouse_motion` implies `mouse`; `cursor=(x,y)` overrides `hide_cursor` |
| Rendering | `color_depth`, `default_style`, `background`, `background_style`, `diff`, `sync_output`, `batch_writes`, `flush`, `unicode`, `trim_rows` | `background=""` = transparent cells; `" "` + a style = full-bleed backdrop |
| Timing | `max_fps`, `input_timeout` | `input_timeout` doubles as the loop's select timeout |
| I/O | `output`, `quiet` | any file-like object; `quiet` disables all writes and all restore logic |
| Shutdown | `restore_on_exit`, `clear_on_exit` | `clear_on_exit` only matters without an alt screen |
| Diagnostics | `stats` | populates `screen.stats` (`Stats`) |

`Config` is a plain dataclass, so it is copyable (`.replace`), serialisable
(`.to_dict` / `.to_json` / `.from_dict`), inspectable (`.describe()`), and
derivable from the environment (`.from_env()`, which honours `TERM`,
`COLORTERM`, `NO_COLOR` and turns on `quiet` for `TERM=dumb`).

---

## 9. Extension points

- **A new widget**: write a function taking `(target, x, y, …)` and using only
  `put`, `put_runs`, `fill`, `rect`, `hline`. It then works on both `Screen`
  and `Layer` for free.
- **A new glyph set**: add a `BoxChars(...)` constant to `chars.py`.
- **A new event**: add a frozen dataclass in `input.py`, produce it from
  `Parser._final`, add a dispatch branch in `App.dispatch`.
- **A new drawing primitive**: put it on `Buffer` (so layers and headless
  buffers get it), then expose a thin passthrough on `Layer` and `Screen` that
  sets `self._dirty = True`.
- **Custom compositing**: override `Layer.compose_into`; the screen only ever
  calls that.

The one rule: **any mutation must mark dirty**, or the change will not appear
until something else invalidates the frame. `Layer`'s properties do this for
you; if you poke `layer.paint` directly, call `screen.invalidate()`.

---

## 10. Testing strategy

368 tests, all headless, ~0.65 s.

- **Unit**: colour parsing/down-sampling, SGR generation and diffing,
  character widths, wrapping, padding, truncation.
- **Structure**: buffer primitives, wide-character hazards, blit clipping,
  resize anchors, scroll, search, bbox.
- **Semantics**: object lifecycle, id replacement, z-order, visibility,
  offsets, coordinate/id/handle removal.
- **Engine**: byte-level assertions on real output — number of cursor moves,
  presence of `ESC[0K`, absence of `ESC[0m` when a delta suffices, "nothing
  changed emits nothing", byte budgets on a 200×60 screen.
- **The invariant that makes diffing safe**: after every frame,
  `front == composite(layers)` — checked frame by frame across a mutating
  sequence in `test_front_buffer_always_equals_a_full_repaint`.
- **Modes**: a `FakeTTY` (a `StringIO` whose `isatty()` returns `True`)
  exercises the interactive path — alt screen, mouse, focus, paste, title,
  cursor shape, and teardown ordering.
- **Input**: table-driven parsing of keys, modifiers, mouse, wheel, drag,
  paste, focus, split UTF-8 and incomplete sequences.

Separately verified by hand in a real PTY (`pty.fork()` + `TIOCSWINSZ`):
mode emission, key handling through raw mode, and clean exit.

---

## 11. Appendix — bugs found while building

Worth recording, because each one shaped the code:

1. **`blit` wrote into a copy.** `target = dst[a:b]` slices a *new* list;
   `target[:] = …` mutated the copy and the blit silently did nothing — but
   only for x-aligned full-width blits, so offset blits worked and hid the
   bug. Fixed by assigning through `dst[a:b] = …`.
2. **Layers were opaque.** `Buffer` defaulted to filling with spaces, so every
   layer covered the ones below with blanks. Buffers now start `EMPTY`; a space
   you draw explicitly is still opaque.
3. **Style resets vanished between frames.** The renderer started each frame
   assuming a default style, so erasing styled text emitted spaces that
   inherited the previous colour. Fixed by carrying `_term_style` across
   frames.
4. **`EMPTY` is `"\x00"`, not `""`.** Two places compared against the wrong
   sentinel, making empty cells un-trimmable (wasted bytes) and non-blank to
   `run_bounds` (text_at matched invisible cells).
5. **The 256-colour palette cache returned `()`.** A `try/except NameError`
   guard around a module-level default never fired, so `to_rgb()` on any
   256-colour would have raised `IndexError`.
6. **Widget id collisions.** Two `bar()` calls both defaulted to `id="bar"`,
   so the second silently replaced the first and only its label survived.
   Default ids are now position-scoped.
7. **Char-level wrapping mangled words.** The first `split_runs` broke at the
   cell limit, splitting "hello world" into "hello t"/"wo th". Rewritten as
   greedy word wrap with hard-split fallback, and — separately — as a verbatim
   pass when `wrap is None`, because dropping leading spaces was corrupting
   menu markers and table padding.
8. **`Underline["curly"]` raised `KeyError`.** Enum lookup is by uppercase
   name; replaced with an alias table that also accepts `True`, `"wavy"`,
   `"off"`, ints.
9. **Two methods named `draw` on `Screen`.** The second definition won and the
   frame presenter disappeared. Renamed the raw-cell painter to `paint`.
10. **A `rect` property shadowed by a `rect` method.** The geometry helper
    became `bounds`.
11. **`layer.visible = False` did nothing.** A plain slotted attribute cannot
    notify anyone. `visible`, `z`, `offset`, `transparent` and `clip` are now
    properties that mark dirty (and re-sort the stack, so live `z` changes
    work).
12. **Space was unbindable.** `Key(" ")` could not be written as a decorator
    argument by accident-prone humans; it is now `Key("space", " ")`.
13. **`ansi()` dropped the columns of untouched cells.** `Buffer.ansi()` — and
    so `frame_ansi()` and `screenshot()` — skipped `EMPTY` cells instead of
    emitting them as spaces, which put coloured text in different columns than
    `frame()` and `row_text()` did. The two readers of one buffer disagreed
    about geometry, and nothing in the suite compared them. Fixing it exposed
    the companion bug: `rstrip=True` stripped the *finished* string, where the
    trailing spaces were already wrapped in escape codes and invisible to
    `str.rstrip()`; the trim now happens on the cells, before colouring.

Building `tred` on top of the library surfaced five more — `shift+f3` swallowed
as a cursor-position report, dirty tracking that only ever went up, a doubled
newline on whole-line cut, block indent collapsing the selection, and column
memory dying on short lines. They are listed in `HOW_IT_WORKS.html` §15.

# termrender

Turn your terminal into a **rendering engine**. Put text at an `(x, y)`
coordinate, find out what is sitting at a coordinate, move it, restyle it,
remove it — and let the engine emit only the bytes that actually changed.

No dependencies. Python 3.10+. Pure standard library.

```python
from termrender import Screen

with Screen() as screen:
    screen.put(4, 2, "hello", fg="cyan", bold=True, id="greeting")
    screen.draw()                       # nothing shows up until you draw

    screen.at(5, 2).move(10, 4)         # locate by coordinate → move
    screen.draw()

    screen.remove("greeting")           # remove by id
    screen.draw()
```

---

## Contents

- [Why](#why)
- [Install](#install)
- [The model](#the-model)
- [Putting, locating and removing text](#putting-locating-and-removing-text)
- [Styles and colours](#styles-and-colours)
- [Layers](#layers)
- [Input](#input)
- [The app loop](#the-app-loop)
- [Widgets](#widgets)
- [Rendering without a terminal](#rendering-without-a-terminal)
- [Configuration — every knob](#configuration--every-knob)
- [Performance](#performance)
- [Examples](#examples)
- [tred — an editor built on it](#tred--an-editor-built-on-it)
- [Documentation](#documentation)
- [API cheat sheet](#api-cheat-sheet)
- [Tests](#tests)

---

## Why

Most terminal libraries give you widgets and a layout system. `termrender`
gives you the layer underneath: **a grid of cells you address directly**.

- **Immediate mode.** You decide what is on screen each frame; there is no
  widget tree to fight with.
- **Objects when you want them.** `put()` returns a handle that remembers the
  cells it owns, so text can be moved and deleted without leaving artefacts.
- **Real diffing.** Front buffer vs. back buffer, grouped into runs, with
  minimal SGR deltas and `clear-to-EOL` collapsing. Moving one label on a
  200×60 screen costs ~37 bytes.
- **Correct text.** Double-width CJK and emoji, zero-width combining marks,
  word wrapping, tabs, clipping — all handled at the cell level.
- **Testable.** `quiet=True` renders to memory; `screen.frame()` gives you the
  exact characters a terminal would show.

---

## Install

```bash
pip install termrender          # from PyPI
# or, from a checkout:
pip install -e .
```

---

## The model

```
your drawing calls
        │
        ▼
   Layer 0 (base)   Layer 1 (hud)   Layer 2 (overlay)      ← z-ordered
   ├─ paint buffer (raw cells)                              each layer holds
   └─ objects      (movable, removable text)
        │
        ▼  composite  (bottom → top, empty cells are transparent)
   back buffer  ──diff──▶  escape codes  ──▶  terminal
        ▲                      │
        └── front buffer ◀─────┘  (what the terminal currently shows)
```

| Concept | Class | What it is |
|---|---|---|
| Cell | `Cell` | one character + one style |
| Grid | `Buffer` | a `width × height` array of cells; drawable on its own |
| Surface | `Layer` | a buffer + a registry of text objects + z/offset/visibility |
| Handle | `TextObject` | text that knows where it lives; move / restyle / remove |
| Engine | `Screen` | owns the layers, composites, diffs and writes bytes |
| Loop | `App` | ticks, input dispatch, frame-rate cap |

Coordinates are **0-based, origin top-left**: `x` is the column, `y` the row.

---

## Putting, locating and removing text

### Put

```python
obj = screen.put(x, y, text, style=None, *, id=None, layer=None,
                 wrap=None, align="left", clip=True, visible=True, z=0, **style_kwargs)
```

All of these mean the same thing:

```python
screen.put(0, 0, "x", fg="red", bold=True)
screen.put(0, 0, "x", "red")
screen.put(0, 0, "x", Color.ansi(1))
screen.put(0, 0, "x", {"fg": "red", "bold": True})
screen.put(0, 0, "x", Style(fg=Color.hex("#ff0000"), bold=True))
```

Re-using an `id` replaces that object in place — perfect for HUD readouts that
change every frame:

```python
screen.put(2, 0, f"hp {hp}", id="hp", fg="green")   # one line, no cleanup
```

Mixed styles on one line:

```python
screen.put_runs(2, 0, [("HP ", dim), ("30", "red"), (" / 30", dim)], id="hp")
```

### Locate

```python
screen.at(x, y)         # → TextObject covering this cell (topmost layer), or None
screen.text_at(x, y)    # → Match(text, x, y, layer, object) for objects *and* raw text
screen.find("needle")   # → [TextObject, ...] whose text contains "needle"
screen.find_all("nee")  # → [Match, ...] everywhere on screen
screen.objects()        # → every text object, in draw order
screen.cell(x, y)       # → the composited Cell (char + style)
screen.char(x, y)       # → just the character
```

`text_at` grows the match left and right until a blank cell, so asking about
any character of a raw-painted word returns the whole word and its origin:

```python
screen.paint(3, 1, "world")
screen.text_at(5, 1)    # <Match 'world' at (3,1) via raw>
```

### Move / restyle

```python
obj = screen.put(0, 0, "player")
obj.move(10, 5)          # absolute
obj.shift(1, 0)          # relative
obj.center()             # centre of the screen
obj.set("player 2", fg="yellow")
obj.append("!")
obj.style_with(bold=True)
obj.hide(); obj.show()
obj.resize(40)           # change the wrap width
obj.rect()               # (x, y, width, height) it covers
obj.contains(x, y)
```

### Remove

```python
screen.remove("score")        # by id
screen.remove(obj)            # by handle
screen.remove((4, 2))         # by coordinate tuple — whatever object is there
screen.remove(4, 2)           # same, positional
screen.remove(4, 2, width=10) # raw rectangle at a position
screen.erase(4, 2, 10, 1)     # raw rectangle erase (x, y, w, h)
screen.clear()                # wipe the base layer
obj.remove()                  # from the handle itself
```

Raw painting is the cheap path — no handle, no bookkeeping, erased by
rectangle:

```python
screen.paint(x, y, text, fg="blue")   # cells only
screen.erase(x, y, w, h)              # make them transparent again
```

### Shapes

```python
screen.rect(x, y, w, h, fg="blue", title="panel", chars="rounded", fill=" ")
screen.hline(x, y, length, "─");  screen.vline(x, y, length, "│")
screen.line(x0, y0, x1, y1, "•")          # Bresenham
screen.fill(x, y, w, h, "░", bg="#222")
screen.blit(some_buffer, x, y)            # stamp another grid
screen.scroll(lines=1)                    # shift raw content (log views)
```

`chars` accepts `"rounded" | "sharp" | "double" | "heavy" | "ascii"` or a
custom `BoxChars(...)`. With `Config(unicode=False)` every box falls back to
`+ - |` automatically.

---

## Styles and colours

```python
from termrender import Style, Color, Underline, ColorDepth

Style(fg="red", bg="#102030", bold=True, dim=False, italic=True,
      underline="curly", underline_color="orange", blink=False,
      reverse=False, hidden=False, strike=False, link="https://…")
```

Colours accept a name, a hex string, an int, an `(r, g, b)` tuple, or a
constructed value:

```python
Color.parse("red")            Color.ansi(9)          Color.c256(208)
Color.hex("#ff8800")          Color.rgb(255, 136, 0) Color.hsv(210, 0.8, 1.0)
Color.lerp(a, b, 0.5)         colour.contrast(bg)    colour.luminance()
```

Every colour is down-sampled to the terminal's real capability
(`ColorDepth.AUTO` sniffs `COLORTERM` / `TERM` / `NO_COLOR`), so the same code
looks right on a truecolour terminal, an 8-colour console, or `TERM=dumb`.

Styles are frozen, hashable and shared between cells, and the renderer emits
only the *difference* between two styles (`bold` off is `22`, not a full
reset), which keeps byte counts tiny.

---

## Layers

```python
hud = screen.layer("hud", z=10)
hud.put(2, 0, "score 0", id="score")
hud.move(0, -1)          # scroll the whole layer
hud.visible = False      # hide it
screen.remove_layer("hud")
```

- Layers composite bottom-to-top by `z`; **empty cells are transparent**, so a
  layer only covers what it paints.
- Each layer has its own object registry and its own offset — moving a layer
  is free (no per-cell work).
- `screen.base` is the default layer; `screen.put`/`paint`/`erase` target it
  unless you pass `layer="hud"`.

---

## Input

`screen.poll(timeout)` returns typed events — no byte parsing on your side:

```python
for event in screen.poll(0.0):
    match event:
        case Key(name="up"):            ...
        case Key(char=c) if c.isalpha(): ...
        case MouseEvent(action="press", x=x, y=y): ...
        case PasteEvent(text=text):     ...
        case ResizeEvent(width=w, height=h): ...
```

Enable what you need in the config: `mouse`, `mouse_motion`, `focus_events`,
`bracketed_paste`, `application_keys`. Keys arrive with canonical names
(`"up"`, `"f5"`, `"ctrl-c"`, `"shift-tab"`, `"pageup"`) plus `ctrl` / `alt` /
`shift` flags, and `key.matches("q", "escape")` makes quit handling a one-liner.

---

## The app loop

```python
from termrender import App, Screen

app = App(Screen(width=60, height=20, mouse=True, stats=True))
app.quit_keys = ("q", "ctrl-c", "escape")

@app.on_start
def build(screen): ...

@app.on_tick                       # dt is seconds since the last frame
def tick(dt, screen): ...

@app.on_key("up", "w")
def up(key, screen): ...

@app.on_mouse
def click(event, screen): ...

app.run(fps=60)
```

Hooks: `start`, `stop`, `tick`, `key`, `mouse`, `resize`, `focus`, `paste`,
`event` (catch-all). Use `app.step()` to drive frames from your own loop, or
`app.run(update=fn, draw=False)` to take full control.

---

## Widgets

Everything in `termrender.widgets` accepts a `Screen` **or** a `Layer`:

```python
from termrender import widgets

widgets.panel(screen, x, y, w, h, title="cpu", fg="blue")      # → inner rect
widgets.bar(screen, x, y, 24, value, maximum=100, label="%",
            gradient=["#22c55e", "#eab308", "#ef4444"])
widgets.sparkline(screen, x, y, values, width=24, fg="green")
widgets.heatmap(screen, x, y, grid, palette=["#001133", "#00ffcc"])
widgets.table(screen, x, y, headers, rows, align=["left", "right"], border=True)
widgets.menu(screen, x, y, items, selected=i)
widgets.text_block(screen, x, y, paragraph, width=40, align="center")
widgets.key_hints(screen, x, screen.height - 1, [("q", "quit"), ("↑↓", "move")])
widgets.gradient_text(screen, x, y, "spectrum", ["#f00", "#0f0", "#00f"])
```

---

## Rendering without a terminal

```python
screen = Screen(Config(width=40, height=10, quiet=True))
screen.put(0, 0, "no terminal involved")
screen.frame()        # plain text
screen.frame_ansi()   # with colour — pipe it into a terminal or a file
screen.buffer()       # a copy of the composited Buffer
```

`quiet=True` never writes a byte, never touches terminal modes, and still runs
the full pipeline — ideal for tests, golden files, docs and CI. There is also a
one-shot helper:

```python
from termrender import render
print(render(24, 3, lambda s: s.put(1, 1, "one shot", fg="yellow")))
```

---

## Configuration — every knob

```python
from termrender import Config, ColorDepth, Style, Screen

screen = Screen(Config(
    width=120, height=40,          # None = auto-detect
    follow_resize=True,            # re-layout on SIGWINCH
    alt_screen=True,               # keep the user's scrollback
    hide_cursor=True,              # or cursor=(x, y) to place it
    cursor_shape="bar",            # block | underline | bar (± -blink)
    color_depth=ColorDepth.TRUECOLOR,
    background="",                 # "" = transparent, " " = painted backdrop
    background_style=Style(),
    diff=True,                     # incremental vs. full repaint
    sync_output=True,              # synchronized updates → no tearing
    trim_rows=True,                # collapse trailing blanks into EL
    batch_writes=0,                # chunk the write, 0 = single write
    flush=True,
    unicode=True,                  # False → ASCII box drawing
    mouse=False, mouse_motion=False,
    focus_events=False, bracketed_paste=False,
    application_keys=True, line_wrap=False,
    title=None, clear_scrollback=False,
    max_fps=None, input_timeout=0.0,
    output=None,                   # any file-like object
    quiet=False,
    restore_on_exit=True, clear_on_exit=True,
    stats=False,
))
```

Or pass them straight to `Screen` — keywords are merged over the config:

```python
screen = Screen(Config(color_depth="basic"), width=80, height=24, mouse=True)
```

`Config` is a plain dataclass: `.replace(**changes)`, `.to_dict()`,
`.from_dict()`, `.to_json()`, `.describe()`, `.from_env()`. With `stats=True`
you get `screen.stats` — frames, fps, cells changed, bytes written and
composite/draw timings per frame.

---

## Performance

Measured on this machine (pure Python, no C extension):

| Screen | Scenario | Cost per frame | Bytes emitted |
|---|---|---|---|
| 80×24 | one moving label | 0.7 ms | 33 B |
| 200×60 | one moving label | 4.1 ms | 37 B |
| 200×60 | full-screen churn (6 000 cells) | 18 ms | — |

The tricks that get you there:

- interned `Cell` objects → the diff fast path is an identity check;
- changed cells collected in one `zip` comprehension, then grouped into runs;
- one cursor move per run, not per cell;
- style *deltas* instead of full SGR resets;
- trailing blanks collapsed into a single `clear-to-EOL`;
- synchronized-update wrapping so the terminal presents the frame atomically;
- layers only re-bake their objects when something changed.

---

## Examples

```bash
python examples/01_hello.py       # put / locate / move / remove
python examples/02_bounce.py      # app loop, keys, mouse, fps counter
python examples/03_dashboard.py   # layers + widgets + live data
python examples/04_snake.py       # a real game in ~150 lines
python examples/05_capture.py     # headless rendering, no terminal needed
```

---

## tred — an editor built on it

The same distribution ships `tred`, a real text editor written against the
**public API only**. There are no editor hooks in the engine: everything it
needs — styled runs, layers, search that returns handles, raw escape output,
headless frames — is what you get too.

```bash
python -m tred path/to/file.py       # or: tred file.py
```

- **Syntax highlighting** for Python, JavaScript/TypeScript, the C family
  (c, cpp, cs, go, java, kt, rs, swift, zig), shell, JSON/TOML/YAML/INI and
  Markdown, plus a plain fallback. The highlighter is a per-line state machine,
  so triple-quoted strings and `/* */` comments carry across rows.
- **A viewport, not a redraw**: gutter, scroll margins, `ensure_visible`,
  wrapped-line awareness and cached per-row highlight runs that are invalidated
  from the row you typed on and refilled forward. A keystroke re-highlights one
  row, never the document.
- **74 bindings over 63 commands**: word/line/page motion with a remembered
  column goal, block indent and dedent, comment toggling, cut/copy/paste
  (OSC 52 to the system clipboard), search with `f3`/`shift+f3`, goto-line,
  auto-pairing brackets and quotes, undo that coalesces typing into steps,
  mouse selection, drag and wheel.
- **Two themes** (`dark`, `light`) and `cycle_syntax` when the extension lied.
- **134 tests** drive it headlessly through `Screen(quiet=True)` — the same
  capture API you have, so the whole editor is tested without a terminal.

~2 900 lines in four files: `document.py` (text + undo), `syntax.py`
(highlighting), `view.py` (layout + overlays), `app.py` (bindings + commands).

---

## Documentation

| File | What it is |
|---|---|
| [`HOW_IT_WORKS.html`](HOW_IT_WORKS.html) | An illustrated page — open it in a browser. Every module, the render pipeline stage by stage, colour handling, hard-won details, six worked examples, and real frames captured from the library while the page was built. Self-contained: no network, no assets. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | The same material in prose, plus an appendix of the bugs found building it and what each one taught. |
| [`examples/`](examples/) | Five runnable programs, smallest first. |

---

## API cheat sheet

| Task | Call |
|---|---|
| Put text | `screen.put(x, y, "t", fg="red", id="k")` |
| Put styled runs | `screen.put_runs(x, y, [("a", red), ("b", blue)])` |
| Paint raw cells | `screen.paint(x, y, "t")` |
| Paint styled runs | `screen.paint_runs(x, y, [("a", red), ("b", blue)])` |
| Locate object | `screen.at(x, y)` |
| Locate any text | `screen.text_at(x, y)` |
| Search objects | `screen.find("needle")` |
| Search everything | `screen.find_all("needle")` |
| Move | `screen.at(x, y).move(nx, ny)` |
| Remove by id | `screen.remove("k")` |
| Remove by position | `screen.remove((x, y))` |
| Erase rectangle | `screen.erase(x, y, w, h)` |
| Read a cell | `screen.cell(x, y)` / `screen.char(x, y)` |
| Box / line / fill | `screen.rect(...)` `screen.line(...)` `screen.fill(...)` |
| Layers | `screen.layer("hud", z=5)` |
| Present a frame | `screen.draw()` (`render()` / `present()` are aliases) |
| Force full repaint | `screen.draw(force=True)` |
| Resize | `screen.resize(w, h)` |
| Cursor | `screen.set_cursor(x, y)` / `screen.set_cursor(None)` |
| Title | `screen.set_title("…")` |
| Input | `screen.poll(timeout)` |
| Capture | `screen.frame()` / `screen.frame_ansi()` (`screenshot` is an alias) |
| Clipboard (OSC 52) | `screen.copy_to_clipboard("text")` |
| Raw escape output | `screen.send("\x1b[?25l")` |
| Lifecycle | `with Screen() as s:` or `s.start()` / `s.stop()` |

---

## Tests

```bash
pip install -e ".[dev]"
pytest            # 507 tests, no terminal required
ruff check .      # clean
```

The suite covers colour parsing and down-sampling, style diffing, character
widths (CJK, emoji, combining marks), wrapping, the buffer grid, layers and
z-order, object lifecycle, the diff renderer's byte output, terminal mode
setup/teardown, resizing, the input parser (keys, modifiers, mouse, paste,
focus), widgets and the app loop — 373 tests for the engine, plus 134 that
drive the `tred` editor headlessly through `quiet=True` screens.

One invariant is asserted explicitly, because everything else rests on it:
after a frame, the front buffer must equal a from-scratch full repaint of the
same scene (`tests/test_engine.py::test_front_buffer_always_equals_a_full_repaint`).
Diffing may only ever change *how many bytes* are written, never *what ends up
on screen*.

## License

MIT

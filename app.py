"""The event loop: ticks, keys, mouse, resize — with a frame-rate cap."""

from __future__ import annotations

import time
from collections.abc import Callable

from .config import Config
from .input import Event, FocusEvent, Key, MouseEvent, PasteEvent, ResizeEvent, is_quit
from .screen import Screen


class App:
    """Drives a :class:`~termrender.screen.Screen` for you.

    Register callbacks, then call :meth:`run`::

        from termrender import App

        app = App(width=60, height=12)

        @app.on_start
        def build(screen):
            screen.put(2, 1, "press q to quit", fg="cyan")

        @app.on_key("q", "ctrl-c")
        def quit_(key, screen):
            app.stop()

        @app.on_tick
        def tick(dt, screen):
            screen.put(2, 3, f"uptime {screen.stats.frames}", id="up")

        app.run(fps=30)

    Every handler receives the event (where there is one) plus the screen, so
    you never have to keep your own reference. Handlers may also be registered
    imperatively with :meth:`on` — ``app.on("key", fn)``.
    """

    def __init__(self, screen: Screen | None = None, /, **config_overrides: object) -> None:
        self.screen = screen if screen is not None else Screen(Config(**config_overrides))  # type: ignore[arg-type]
        self.running = False
        self.quit_keys: tuple[str, ...] = ("ctrl-c",)
        self._handlers: dict[str, list[Callable[..., object]]] = {
            "start": [], "stop": [], "tick": [], "key": [], "mouse": [],
            "resize": [], "focus": [], "paste": [], "event": [],
        }
        self._key_bindings: dict[str, list[Callable[..., object]]] = {}
        self._frames = 0
        self._clock = time.perf_counter()
        self._session_start = self._clock

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def on(self, event: str, handler: Callable[..., object]) -> Callable[..., object]:
        """Register ``handler`` for ``event`` (also usable as a decorator)."""
        key = event.lower()
        if key not in self._handlers:
            raise ValueError(f"unknown event {event!r}; choose from {sorted(self._handlers)}")
        self._handlers[key].append(handler)
        return handler

    # decorator aliases -------------------------------------------------
    @property
    def on_start(self):
        return lambda fn: self.on("start", fn)

    @property
    def on_stop(self):
        return lambda fn: self.on("stop", fn)

    @property
    def on_tick(self):
        return lambda fn: self.on("tick", fn)

    @property
    def on_key(self):
        """``@app.on_key`` for every key, ``@app.on_key("q", "up")`` for some."""

        def decorator(*names_or_fn):
            if len(names_or_fn) == 1 and callable(names_or_fn[0]):
                return self.on("key", names_or_fn[0])
            def wrapper(fn: Callable[..., object]) -> Callable[..., object]:
                for name in names_or_fn:
                    self._key_bindings.setdefault(name, []).append(fn)
                return fn
            return wrapper

        return decorator

    @property
    def on_mouse(self):
        return lambda fn: self.on("mouse", fn)

    @property
    def on_resize(self):
        return lambda fn: self.on("resize", fn)

    @property
    def on_focus(self):
        return lambda fn: self.on("focus", fn)

    @property
    def on_paste(self):
        return lambda fn: self.on("paste", fn)

    @property
    def on_event(self):
        """Catch-all handler, called for every event before the specific ones."""
        return lambda fn: self.on("event", fn)

    def bind(self, key: str, handler: Callable[..., object]) -> None:
        """Bind one key name (``"q"``, ``"up"``, ``"ctrl-s"``)."""
        self._key_bindings.setdefault(key, []).append(handler)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def dispatch(self, event: Event) -> None:
        for handler in self._handlers["event"]:
            handler(event, self.screen)

        if isinstance(event, Key):
            for handler in self._handlers["key"]:
                handler(event, self.screen)
            for handler in self._key_bindings.get(event.name, ()):
                handler(event, self.screen)
            if self.quit_keys and is_quit(event, self.quit_keys):
                self.stop()
        elif isinstance(event, MouseEvent):
            for handler in self._handlers["mouse"]:
                handler(event, self.screen)
        elif isinstance(event, ResizeEvent):
            for handler in self._handlers["resize"]:
                handler(event, self.screen)
        elif isinstance(event, FocusEvent):
            for handler in self._handlers["focus"]:
                handler(event, self.screen)
        elif isinstance(event, PasteEvent):
            for handler in self._handlers["paste"]:
                handler(event, self.screen)

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------
    def run(
        self,
        fps: float | None = None,
        *,
        max_frames: int | None = None,
        update: Callable[[float, Screen], object] | None = None,
        draw: bool = True,
    ) -> int:
        """Run until :meth:`stop` is called. Returns the number of frames drawn."""
        screen = self.screen
        if not screen.started:
            screen.start()
        if fps is None:
            fps = screen.config.max_fps
        budget = (1.0 / fps) if fps and fps > 0 else 0.0

        self.running = True
        self._clock = time.perf_counter()
        self._session_start = self._clock
        self._frames = 0
        for handler in self._handlers["start"]:
            handler(screen)
        if update is not None:
            update(0.0, screen)
        if draw:
            screen.draw(force=True)

        try:
            while self.running:
                frame_start = time.perf_counter()
                dt = frame_start - self._clock
                self._clock = frame_start

                wait = budget if budget else max(0.0, screen.config.input_timeout)
                for event in screen.poll(wait if wait > 0 else 0.0):
                    self.dispatch(event)
                    if not self.running:
                        break

                if not self.running:
                    break
                for handler in self._handlers["tick"]:
                    handler(dt, screen)
                if update is not None:
                    update(dt, screen)
                if draw:
                    screen.draw()

                self._frames += 1
                if screen.config.stats:
                    screen.stats.fps = self._frames / max(1e-9, time.perf_counter() - self._session_start)

                if max_frames is not None and self._frames >= max_frames:
                    break

                if budget:
                    elapsed = time.perf_counter() - frame_start
                    remaining = budget - elapsed
                    if remaining > 0.001:
                        time.sleep(remaining)
        except KeyboardInterrupt:  # pragma: no cover - interactive
            pass
        finally:
            for handler in self._handlers["stop"]:
                handler(screen)
            self.running = False
        return self._frames

    def step(self, dt: float = 0.0, *, draw: bool = True) -> list[Event]:
        """Advance exactly one frame (drives the app from your own loop)."""
        events = self.screen.poll(0.0)
        for event in events:
            self.dispatch(event)
        for handler in self._handlers["tick"]:
            handler(dt, self.screen)
        if draw:
            self.screen.draw()
        self._frames += 1
        return events

    def stop(self) -> None:
        """Ask the loop to exit after the current frame."""
        self.running = False

    @property
    def frames(self) -> int:
        return self._frames


__all__ = ["App"]

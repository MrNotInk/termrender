"""Small, cell-based 2D game toolkit built on termrender.

Coordinates and velocities are measured in terminal cells and cells/second.
This is a character-cell engine, not a pixel renderer or a physics simulator.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from .app import App
from .input import Event
from .screen import Screen
from .style import Style
from .text import char_width


@dataclass(frozen=True)
class Rect:
    """A floating-point, half-open axis-aligned rectangle."""
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("rectangle dimensions must be positive")

    def intersects(self, other: Rect) -> bool:
        return (self.x < other.x + other.width and self.x + self.width > other.x
                and self.y < other.y + other.height and self.y + self.height > other.y)


def _check_glyphs(text: str) -> None:
    if any(char_width(ch) != 1 or ch in "\r\n\t" for ch in text):
        raise ValueError("game glyphs must each occupy exactly one terminal cell")


@dataclass(frozen=True)
class Sprite:
    """A multi-line sprite. Spaces are transparent; all glyphs must be one cell."""
    rows: tuple[str, ...]
    style: Style = field(default_factory=Style)

    def __post_init__(self) -> None:
        # Accept lists as well as tuples while keeping the value immutable.
        object.__setattr__(self, "rows", tuple(self.rows))
        if not self.rows or not any(self.rows):
            raise ValueError("a sprite must contain at least one cell")
        for row in self.rows:
            _check_glyphs(row)

    @classmethod
    def from_text(cls, text: str, style: Style | None = None) -> Sprite:
        return cls(tuple(text.split("\n")), style or Style())

    @property
    def width(self) -> int:
        return max(map(len, self.rows))

    @property
    def height(self) -> int:
        return len(self.rows)


@dataclass(eq=False)
class Entity:
    """A sprite with a rectangular hitbox and optional velocity.

    Subclass and override ``update`` or ``on_event`` for game-specific logic.
    Collision uses the sprite's rectangular bounding box, including spaces.
    """
    x: float
    y: float
    sprite: Sprite
    vx: float = 0.0
    vy: float = 0.0
    solid: bool = False
    visible: bool = True
    z: int = 0
    tag: str = ""

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.sprite.width, self.sprite.height)

    def update(self, dt: float, scene: Scene) -> None:
        scene.move(self, self.vx * dt, self.vy * dt)

    def on_event(self, event: Event, scene: Scene) -> None:
        pass


@dataclass(frozen=True)
class Tile:
    glyph: str
    solid: bool = False
    style: Style = field(default_factory=Style)

    def __post_init__(self) -> None:
        if len(self.glyph) != 1:
            raise ValueError("tile glyph must be one character")
        _check_glyphs(self.glyph)


class TileMap:
    """A rectangular map of single-character tile keys. Undefined keys are empty."""
    def __init__(self, rows: Iterable[str], tiles: dict[str, Tile], *, x: int = 0, y: int = 0):
        self.rows = tuple(rows)
        if not self.rows or not self.rows[0] or any(len(row) != len(self.rows[0]) for row in self.rows):
            raise ValueError("tile map must be non-empty and rectangular")
        self.tiles = dict(tiles)
        self.x, self.y = x, y
        self.width, self.height = len(self.rows[0]), len(self.rows)

    def solid_rects(self) -> Iterable[Rect]:
        for y, row in enumerate(self.rows):
            for x, key in enumerate(row):
                tile = self.tiles.get(key)
                if tile is not None and tile.solid:
                    yield Rect(self.x + x, self.y + y, 1, 1)


@dataclass
class Camera:
    """World coordinate at the top-left of the screen, in cells."""
    x: int = 0
    y: int = 0

    def center_on(self, entity: Entity, screen: Screen) -> None:
        self.x = math.floor(entity.x + entity.sprite.width / 2 - screen.width / 2)
        self.y = math.floor(entity.y + entity.sprite.height / 2 - screen.height / 2)


class Scene:
    """Owns entities, a tile map and a camera. Override hooks to make a game."""
    def __init__(self, tilemap: TileMap | None = None, camera: Camera | None = None):
        self.tilemap = tilemap
        self.camera = camera or Camera()
        self.entities: list[Entity] = []

    def add(self, entity: Entity) -> Entity:
        if entity not in self.entities:
            self.entities.append(entity)
        return entity

    def remove(self, entity: Entity) -> None:
        self.entities.remove(entity)

    def overlaps(self, entity: Entity, *, tag: str | None = None) -> list[Entity]:
        return [other for other in self.entities if other is not entity
                and (tag is None or other.tag == tag) and entity.rect.intersects(other.rect)]

    def move(self, entity: Entity, dx: float, dy: float) -> tuple[float, float]:
        """Move and slide against solid tiles/entities, returning actual displacement.

        X is resolved before Y. Sweeping prevents tunneling; initially overlapping
        obstacles do not trap an entity, so callers should spawn in free space.
        All entities collide with solids, regardless of their own ``solid`` flag.
        """
        obstacles = list(self.tilemap.solid_rects()) if self.tilemap else []
        obstacles.extend(other.rect for other in self.entities if other is not entity and other.solid)
        start_x, start_y = entity.x, entity.y
        for axis, distance in (("x", dx), ("y", dy)):
            box = entity.rect
            allowed = distance
            for wall in obstacles:
                pos, size = (box.x, box.width) if axis == "x" else (box.y, box.height)
                wpos, wsize = (wall.x, wall.width) if axis == "x" else (wall.y, wall.height)
                cross, cross_size = (box.y, box.height) if axis == "x" else (box.x, box.width)
                wcross, wcross_size = (wall.y, wall.height) if axis == "x" else (wall.x, wall.width)
                if cross >= wcross + wcross_size or cross + cross_size <= wcross:
                    continue
                if distance > 0 and pos + size <= wpos:
                    allowed = min(allowed, wpos - (pos + size))
                elif distance < 0 and pos >= wpos + wsize:
                    allowed = max(allowed, wpos + wsize - pos)
            setattr(entity, axis, getattr(entity, axis) + allowed)
        return entity.x - start_x, entity.y - start_y

    def on_event(self, event: Event) -> None:
        for entity in tuple(self.entities):
            if entity in self.entities:
                entity.on_event(event, self)

    def update(self, dt: float) -> None:
        for entity in tuple(self.entities):
            if entity in self.entities:
                entity.update(dt, self)

    def draw(self, screen: Screen, layer: str) -> None:
        """Paint the world into the engine's layer. Override for a HUD if needed."""
        camera = self.camera
        if self.tilemap:
            m = self.tilemap
            for y in range(max(0, camera.y - m.y), min(m.height, camera.y + screen.height - m.y)):
                for x in range(max(0, camera.x - m.x), min(m.width, camera.x + screen.width - m.x)):
                    tile = m.tiles.get(m.rows[y][x])
                    if tile and tile.glyph != " ":
                        screen.paint(m.x + x - camera.x, m.y + y - camera.y, tile.glyph, tile.style, layer=layer)
        for entity in sorted(self.entities, key=lambda item: item.z):
            if not entity.visible:
                continue
            ex, ey = math.floor(entity.x) - camera.x, math.floor(entity.y) - camera.y
            for y, row in enumerate(entity.sprite.rows):
                sy = ey + y
                if not 0 <= sy < screen.height:
                    continue
                for x, glyph in enumerate(row):
                    sx = ex + x
                    if glyph != " " and 0 <= sx < screen.width:
                        screen.paint(sx, sy, glyph, entity.sprite.style, layer=layer)


class Game:
    """Connect a scene to termrender's input, rendering and frame loop."""
    def __init__(self, scene: Scene, screen: Screen | None = None, *, layer: str = "game"):
        self.scene = scene
        self.screen = screen if screen is not None else Screen()
        if self.screen.get_layer(layer) is not None:
            raise ValueError(f"layer {layer!r} already exists; choose a dedicated game layer")
        self.layer = layer
        self.screen.layer(layer)
        self.app = App(self.screen)
        self.app.quit_keys = ("q", "escape", "ctrl-c")
        self.app.on("event", lambda event, screen: self.scene.on_event(event))
        self.app.on("tick", lambda dt, screen: self.scene.update(dt))
        self.app.on("tick", lambda dt, screen: self.render())

    def render(self) -> None:
        """Repaint the dedicated game layer without touching other screen layers."""
        self.screen.clear(layer=self.layer)
        self.scene.draw(self.screen, self.layer)

    def step(self, dt: float, events: Iterable[Event] = ()) -> None:
        """Advance deterministically without polling; useful for headless tests."""
        if dt < 0 or not math.isfinite(dt):
            raise ValueError("dt must be finite and non-negative")
        for event in events:
            self.scene.on_event(event)
        self.scene.update(dt)
        self.render()
        self.screen.draw()

    def run(self, fps: float = 30, *, max_frames: int | None = None) -> int:
        """Run interactively, restoring the terminal when the loop exits."""
        if fps <= 0 or not math.isfinite(fps):
            raise ValueError("fps must be finite and positive")
        was_started = self.screen.started
        try:
            self.render()
            return self.app.run(fps=fps, max_frames=max_frames)
        finally:
            if not was_started:
                self.screen.stop()

    def stop(self) -> None:
        self.app.stop()


__all__ = ["Camera", "Entity", "Game", "Rect", "Scene", "Sprite", "Tile", "TileMap"]

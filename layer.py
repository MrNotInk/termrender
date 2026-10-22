"""Layers — independently drawable, z-ordered surfaces."""

from __future__ import annotations

from collections.abc import Iterator

from .buffer import Buffer
from .chars import BoxChars
from .objects import TextObject, next_id
from .style import Style, normalize_style


class Layer:
    """A named, z-ordered surface that holds both raw cells and text objects.

    A layer gives you two drawing models at once:

    * **raw drawing** — :meth:`draw`, :meth:`fill`, :meth:`rect`, :meth:`line`
      write cells straight into the layer's buffer (cheap, but only erasable
      by rectangle);
    * **objects** — :meth:`put` returns a :class:`~termrender.objects.TextObject`
      that remembers the cells it owns, so you can :meth:`move` it, restyle
      it, or :meth:`remove` it by id or by coordinate.

    Layers compose bottom-to-top by ``z``. Empty cells are transparent, so a
    layer only covers what it actually paints.
    """

    __slots__ = (
        "_clip",
        "_obj_buffer",
        "_objects",
        "_objects_dirty",
        "_offset",
        "_transparent",
        "_visible",
        "_z",
        "height",
        "name",
        "notify",
        "paint",
        "width",
    )

    def __init__(
        self,
        name: str,
        width: int,
        height: int,
        *,
        z: int = 0,
        visible: bool = True,
        transparent: bool = True,
        offset: tuple[int, int] = (0, 0),
        clip: bool = True,
        default_style: Style | None = None,
        notify: callable | None = None,
    ) -> None:
        self.name = name
        self.width = width
        self.height = height
        self._z = int(z)
        self._visible = bool(visible)
        self._transparent = bool(transparent)
        self._offset = (int(offset[0]), int(offset[1]))
        self._clip = bool(clip)
        self.notify = notify
        self.paint = Buffer(width, height, default_style=default_style)
        self._obj_buffer = Buffer(width, height)
        self._objects: dict[str, TextObject] = {}
        self._objects_dirty = True

    # ------------------------------------------------------------------
    # Live attributes — assigning any of them invalidates the composite
    # ------------------------------------------------------------------
    @property
    def z(self) -> int:
        """Stacking order; higher draws on top."""
        return self._z

    @z.setter
    def z(self, value: int) -> None:
        self._z = int(value)
        self.mark_dirty()

    @property
    def visible(self) -> bool:
        """When False the layer contributes nothing to the frame."""
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = bool(value)
        self.mark_dirty()

    @property
    def transparent(self) -> bool:
        """When True only non-empty cells cover the layers below."""
        return self._transparent

    @transparent.setter
    def transparent(self, value: bool) -> None:
        self._transparent = bool(value)
        self.mark_dirty()

    @property
    def offset(self) -> tuple[int, int]:
        """Where the layer sits on screen — moving it costs nothing per cell."""
        return self._offset

    @offset.setter
    def offset(self, value: tuple[int, int]) -> None:
        self._offset = (int(value[0]), int(value[1]))
        self.mark_dirty()

    @property
    def clip(self) -> bool:
        """Whether text objects stop at the right edge instead of spilling."""
        return self._clip

    @clip.setter
    def clip(self, value: bool) -> None:
        self._clip = bool(value)
        self.mark_dirty()

    def show(self) -> Layer:
        self.visible = True
        return self

    def hide(self) -> Layer:
        self.visible = False
        return self

    # ------------------------------------------------------------------
    # Objects: put / locate / move / remove
    # ------------------------------------------------------------------
    def put(
        self,
        x: int,
        y: int,
        content: str,
        style: Style | dict | None = None,
        /,
        *,
        id: str | None = None,  # noqa: A002 - deliberate, reads well at call sites
        visible: bool = True,
        clip: bool | None = None,
        wrap: int | None = None,
        align: str = "left",
        z: int = 0,
        base: Style | None = None,
        **kwargs: object,
    ) -> TextObject:
        """Place text at ``(x, y)`` and return a handle you can remove later.

        Pass ``id="score"`` to name it, or let an id be generated. Re-using
        an existing id replaces that object in place (handy for HUD text
        that changes every frame).
        """
        st = normalize_style(style, base=base, **kwargs)
        obj_id = id or next_id("txt")
        existing = self._objects.get(obj_id)
        if existing is not None:
            existing._runs = [(content, st)] if content else []  # noqa: SLF001
            existing.x, existing.y = int(x), int(y)
            if wrap is not None:
                existing.wrap = wrap
            return existing._touch()  # noqa: SLF001

        obj = TextObject(
            self,
            obj_id,
            x,
            y,
            [(content, st)] if content else [],
            visible=visible,
            clip=self.clip if clip is None else clip,
            wrap=wrap,
            align=align,
            z=z,
        )
        self._objects[obj_id] = obj
        self.mark_dirty()
        return obj

    def put_runs(
        self,
        x: int,
        y: int,
        runs,  # Iterable[tuple[str, Style | dict | None]]
        /,
        *,
        id: str | None = None,  # noqa: A002
        **kwargs: object,
    ) -> TextObject:
        """Place mixed-style text: ``[(\"HP \", dim), (\"30\", red)]``."""
        obj = self.put(x, y, "", id=id, **kwargs)
        obj.set_runs(runs)
        return obj

    def object(self, obj_id: str) -> TextObject | None:
        """Look an object up by id."""
        return self._objects.get(obj_id)

    def objects(self) -> list[TextObject]:
        """All objects on this layer, in draw order."""
        return sorted(self._objects.values(), key=lambda o: (o.z, o.y, o.x))

    def ids(self) -> list[str]:
        return list(self._objects)

    def at(self, x: int, y: int) -> TextObject | None:
        """Locate the topmost object covering cell ``(x, y)``."""
        found: TextObject | None = None
        for obj in self._objects.values():
            if (obj.visible and obj.contains(x, y)
                    and (found is None or (obj.z, obj.y, obj.x) >= (found.z, found.y, found.x))):
                found = obj
        return found

    def find(self, content: str) -> list[TextObject]:
        """Locate every object whose text contains ``content``."""
        return [o for o in self.objects() if content in o.text]

    def remove(self, target: str | TextObject | tuple[int, int]) -> bool:
        """Remove an object by id, by handle, or by the coordinate it covers.

        ``layer.remove("score")`` / ``layer.remove(obj)`` /
        ``layer.remove((3, 4))``. Returns True when something was removed.
        """
        if isinstance(target, TextObject):
            return self._drop(target.id)
        if isinstance(target, tuple):
            obj = self.at(int(target[0]), int(target[1]))
            return self._drop(obj.id) if obj else False
        return self._drop(str(target))

    def remove_at(self, x: int, y: int) -> bool:
        """Remove whatever text is at ``(x, y)``.

        Objects are removed whole; raw cells are removed as the contiguous
        run around ``(x, y)``, so erasing one character of a word erases the
        word (mirroring what ``Screen.text_at`` reports).
        """
        obj = self.at(x, y)
        if obj is not None:
            return self._drop(obj.id)
        bounds = self.paint.run_bounds(x, y)
        if bounds is None:
            return False
        start, stop = bounds
        self.paint.erase(start, y, stop - start + 1, 1)
        return True

    def remove_rect(self, x: int, y: int, w: int, h: int = 1) -> int:
        """Remove every object overlapping the rectangle, and erase raw cells."""
        removed = 0
        for obj in list(self._objects.values()):
            ox, oy, ow, oh = obj.rect()
            if ox < x + w and ox + ow > x and oy < y + h and oy + oh > y:
                self._drop(obj.id)
                removed += 1
        self.paint.erase(x, y, w, h)
        return removed

    def clear_objects(self) -> int:
        count = len(self._objects)
        self._objects.clear()
        self.mark_dirty()
        return count

    def _drop(self, obj_id: str) -> bool:
        if obj_id in self._objects:
            del self._objects[obj_id]
            self.mark_dirty()
            return True
        return False

    def __getitem__(self, key: str | tuple[int, int]) -> TextObject | Cell:  # noqa: F821
        if isinstance(key, tuple):
            return self.paint.get(int(key[0]), int(key[1]))
        obj = self._objects.get(str(key))
        if obj is None:
            raise KeyError(key)
        return obj

    def __contains__(self, key: object) -> bool:
        if isinstance(key, tuple):
            return self.at(int(key[0]), int(key[1])) is not None
        return str(key) in self._objects

    def __len__(self) -> int:
        return len(self._objects)

    def __iter__(self) -> Iterator[TextObject]:
        return iter(self.objects())

    # ------------------------------------------------------------------
    # Raw drawing (same surface as Buffer, offset-free)
    # ------------------------------------------------------------------
    def draw(self, x: int, y: int, content: str, style: Style | dict | None = None, /, **kwargs: object) -> int:
        """Paint text directly into the layer buffer (no object handle)."""
        return self.paint.put(x, y, content, normalize_style(style, **kwargs))

    def paint_runs(self, x: int, y: int, runs, *, wrap: bool = False) -> int:
        """Paint raw mixed-style runs (no object handle)."""
        from .style import normalize_style

        used = 0
        for chunk, style in runs:
            if not chunk:
                continue
            used += self.paint.put(x + used, y, chunk, normalize_style(style), wrap=wrap)
        return used

    def erase(self, x: int, y: int, w: int = 1, h: int = 1) -> int:
        return self.paint.erase(x, y, w, h)

    def fill(self, x: int, y: int, w: int, h: int, char: str = " ", style: Style | dict | None = None, /, **kwargs: object) -> int:
        return self.paint.fill(x, y, w, h, char, normalize_style(style, **kwargs))

    def rect(self, x: int, y: int, w: int, h: int, style: Style | dict | None = None, /, *,
             chars: str | BoxChars | None = None, fill: str | None = None,
             fill_style: Style | dict | None = None, title: str = "",
             title_align: str = "left", title_style: Style | dict | None = None,
             **kwargs: object) -> None:
        """Draw a box outline on this layer, optionally filled and titled."""
        st = normalize_style(style, base=self.paint.default_style, **kwargs)
        self.paint.rect(
            x, y, w, h, st,
            chars=chars,
            fill=fill,
            fill_style=normalize_style(fill_style, base=st) if fill_style is not None else st,
            title=title,
            title_align=title_align,
            title_style=normalize_style(title_style, base=st) if title_style is not None else None,
        )

    def box(self, x: int, y: int, w: int, h: int, style: Style | dict | None = None, /, **kwargs: object) -> None:
        """Alias of :meth:`rect`."""
        self.rect(x, y, w, h, style, **kwargs)  # type: ignore[arg-type]

    def hline(self, x: int, y: int, length: int, char: str = "─", style: Style | None = None) -> int:
        return self.paint.hline(x, y, length, char, normalize_style(style))

    def vline(self, x: int, y: int, length: int, char: str = "│", style: Style | None = None) -> int:
        return self.paint.vline(x, y, length, char, normalize_style(style))

    def line(self, x0: int, y0: int, x1: int, y1: int, char: str = "•", style: Style | None = None) -> int:
        return self.paint.line(x0, y0, x1, y1, char, normalize_style(style))

    def blit(self, buffer: Buffer, x: int = 0, y: int = 0, *, transparent: bool = True) -> None:
        self.paint.blit(buffer, x, y, transparent=transparent)

    def clear(self, *, objects: bool = False, char: str = "") -> None:
        """Wipe the layer's pixels (and optionally its objects)."""
        self.paint.clear(char=char)
        if objects:
            self._objects.clear()
        self.mark_dirty()

    def move(self, dx: int, dy: int) -> Layer:
        """Offset the whole layer (cheap scrolling / animation)."""
        self.offset = (self.offset[0] + int(dx), self.offset[1] + int(dy))
        self.mark_dirty()
        return self

    def move_to(self, x: int, y: int) -> Layer:
        self.offset = (int(x), int(y))
        self.mark_dirty()
        return self

    def resize(self, width: int, height: int, *, keep: bool = True) -> None:
        self.width, self.height = int(width), int(height)
        self.paint.resize(self.width, self.height, keep=keep)
        self._obj_buffer = Buffer(self.width, self.height)
        self.mark_dirty()

    def mark_dirty(self) -> None:
        """Flag the object cache for re-baking before the next composite."""
        self._objects_dirty = True
        if self.notify is not None:
            self.notify(self)

    # ------------------------------------------------------------------
    # Compositing
    # ------------------------------------------------------------------
    def bake(self) -> Buffer:
        """Re-rasterise all objects into the object buffer."""
        if self._objects_dirty:
            self._obj_buffer.clear()
            for obj in self.objects():
                obj.paint(self._obj_buffer)
            self._objects_dirty = False
        return self._obj_buffer

    def compose_into(self, target: Buffer) -> None:
        """Blit this layer onto ``target`` at its offset."""
        if not self.visible:
            return
        ox, oy = self.offset
        target.blit(self.paint, ox, oy, transparent=self.transparent)
        if self._objects:
            target.blit(self.bake(), ox, oy, transparent=True)

    def snapshot(self) -> Buffer:
        """A standalone buffer with this layer fully composited."""
        out = Buffer(self.width, self.height, default_style=self.paint.default_style)
        self.compose_into(out)
        return out

    @property
    def dirty(self) -> bool:
        """True when the object cache needs re-baking."""
        return self._objects_dirty

    def __repr__(self) -> str:
        state = "" if self.visible else " hidden"
        return f"<Layer {self.name!r} {self.width}x{self.height} z={self.z} objects={len(self._objects)}{state}>"


__all__ = ["Layer"]

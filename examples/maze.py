"""Collect the stars! Run with: python examples/maze.py"""
from termrender import Entity, Game, Key, Scene, Screen, Sprite, Style, Tile, TileMap

LEVEL = (
    "#############################",
    "#     #        #           *#",
    "# ### # ###### # ####### ## #",
    "# #   #    * # #       #    #",
    "# # ##### ## # ####### #### #",
    "# #     # ## #       #      #",
    "# ##### # ## ####### # ######",
    "#     # #          # #      #",
    "### # # ########## # ###### #",
    "# * #              #       *#",
    "#############################",
)


class Maze(Scene):
    def __init__(self):
        super().__init__(TileMap(LEVEL, {"#": Tile("#", solid=True, style=Style(fg="blue"))}))
        self.player = self.add(Entity(1, 1, Sprite.from_text("@", Style(fg="cyan", bold=True))))
        for y, row in enumerate(LEVEL):
            for x, glyph in enumerate(row):
                if glyph == "*":
                    self.add(Entity(x, y, Sprite.from_text("*", Style(fg="yellow")), tag="star"))
        self.total = sum(entity.tag == "star" for entity in self.entities)
        self.score = 0

    def on_event(self, event):
        if not isinstance(event, Key):
            return
        if event.name == "r":
            self.__init__()
            return
        directions = {"up": (0, -1), "w": (0, -1), "down": (0, 1), "s": (0, 1),
                      "left": (-1, 0), "a": (-1, 0), "right": (1, 0), "d": (1, 0)}
        if event.name in directions and self.score < self.total:
            self.move(self.player, *directions[event.name])
            for star in self.overlaps(self.player, tag="star"):
                self.remove(star)
                self.score += 1

    def draw(self, screen, layer):
        super().draw(screen, layer)
        status = "All stars collected!" if self.score == self.total else f"Stars: {self.score}/{self.total}"
        screen.paint(0, 11, status, fg="yellow", layer=layer)
        screen.paint(0, 12, "Arrows/WASD move  R reset  Q quit", layer=layer)


if __name__ == "__main__":
    Game(Maze(), Screen(width=36, height=13)).run()

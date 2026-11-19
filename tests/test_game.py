import unittest

from termrender import Camera, Entity, Game, Key, Rect, Scene, Screen, Sprite, Style, Tile, TileMap
from examples.maze import LEVEL, Maze


class GameTests(unittest.TestCase):
    def test_rect_and_sprite_validation(self):
        self.assertFalse(Rect(0, 0, 1, 1).intersects(Rect(1, 0, 1, 1)))
        self.assertTrue(Rect(0, 0, 1, 1).intersects(Rect(.5, 0, 1, 1)))
        for value in ("界", "e\u0301", "\t"):
            with self.assertRaises(ValueError):
                Sprite.from_text(value)
        with self.assertRaises(ValueError):
            TileMap(["##", "#"], {})

    def test_swept_collision_and_sliding(self):
        scene = Scene(TileMap([".....", "..#..", "....."], {"#": Tile("#", solid=True)}))
        player = scene.add(Entity(0, 1, Sprite.from_text("@")))
        self.assertEqual(scene.move(player, 20, 1), (1, 1))
        self.assertEqual((player.x, player.y), (1, 2))
        wall = scene.add(Entity(0, 2, Sprite.from_text("X"), solid=True))
        self.assertEqual(scene.move(player, -5, 0), (0, 0))
        scene.remove(wall)
        self.assertEqual(scene.move(player, -5, 0), (-5, 0))

    def test_render_camera_order_transparency_and_cleanup(self):
        screen = Screen(width=5, height=3, quiet=True)
        screen.paint(0, 0, "B")
        scene = Scene(TileMap([".....", ".###.", "....."], {"#": Tile("#", style=Style(fg="red"))}), Camera(1, 1))
        back = scene.add(Entity(1, 1, Sprite.from_text("AB"), z=0))
        front = scene.add(Entity(1, 1, Sprite.from_text(" X"), z=1))
        game = Game(scene, screen)
        game.step(0)
        self.assertEqual(screen.frame().splitlines()[0][:3], "AX#")
        scene.remove(front)
        back.x = 2
        game.step(0)
        self.assertEqual(screen.frame().splitlines()[0][:3], "#AB")
        scene.remove(back)
        scene.camera.x = 20
        game.step(0)
        self.assertEqual(screen.char(0, 0), "B")

    def test_updates_events_and_overlap(self):
        class Actor(Entity):
            def on_event(self, event, scene):
                if isinstance(event, Key):
                    self.vx = 2
        scene = Scene()
        actor = scene.add(Actor(0, 0, Sprite.from_text("@")))
        star = scene.add(Entity(2, 0, Sprite.from_text("*"), tag="star"))
        game = Game(scene, Screen(width=8, height=2, quiet=True))
        game.step(1, [Key("right")])
        self.assertEqual(actor.x, 2)
        self.assertEqual(scene.overlaps(actor, tag="star"), [star])
        with self.assertRaises(ValueError):
            game.step(-1)

    def test_maze_is_playable_and_resettable(self):
        self.assertEqual(len({len(row) for row in LEVEL}), 1)
        scene = Maze()
        game = Game(scene, Screen(width=36, height=13, quiet=True))
        game.step(0, [Key("right"), Key("down")])
        self.assertEqual((scene.player.x, scene.player.y), (2, 1))  # wall below
        game.step(0, [Key("r")])
        self.assertEqual((scene.player.x, scene.player.y, scene.score), (1, 1, 0))
        self.assertIn("Stars: 0/4", game.screen.frame())

    def test_all_maze_stars_are_reachable_and_collectible(self):
        from collections import deque

        scene = Maze()
        game = Game(scene, Screen(width=36, height=13, quiet=True))
        start = (1, 1)
        queue = deque([start])
        paths = {start: []}
        directions = (("right", 1, 0), ("left", -1, 0), ("down", 0, 1), ("up", 0, -1))
        while queue:
            x, y = queue.popleft()
            for name, dx, dy in directions:
                nx, ny = x + dx, y + dy
                if (0 <= ny < len(LEVEL) and 0 <= nx < len(LEVEL[ny])
                        and LEVEL[ny][nx] != "#" and (nx, ny) not in paths):
                    paths[nx, ny] = paths[x, y] + [name]
                    queue.append((nx, ny))
        stars = [(int(e.x), int(e.y)) for e in scene.entities if e.tag == "star"]
        for star in stars:
            self.assertIn(star, paths)
        # Follow each path from spawn, resetting position only (stars remain collected).
        for star in stars:
            scene.player.x, scene.player.y = start
            game.step(0, [Key(name) for name in paths[star]])
        self.assertEqual(scene.score, scene.total)
        self.assertIn("All stars collected!", game.screen.frame())

    def test_loop_and_layer_ownership(self):
        screen = Screen(width=4, height=3, quiet=True)
        screen.layer("hud")
        with self.assertRaises(ValueError):
            Game(Scene(), screen, layer="hud")
        game = Game(Scene(), screen)
        self.assertEqual(game.run(fps=1000, max_frames=2), 2)
        self.assertFalse(screen.started)


if __name__ == "__main__":
    unittest.main()

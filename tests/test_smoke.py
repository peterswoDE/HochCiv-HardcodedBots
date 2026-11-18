"""Smoke tests: the worker starts, a full game runs to the end without refused-action errors."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "template"), str(ROOT)]

from hcbots.bridge import Bridge          # noqa: E402
from hcbots.runner import play_game       # noqa: E402
from bots.rusher.bot import Rusher        # noqa: E402
from example_bot import ExampleBot        # noqa: E402


def seats(n, py):
    s = [{"kind": "ki", "kiLevel": "schwer"} for _ in range(n)]
    s[py] = {"kind": "py"}
    return s


class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.br = Bridge().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.br.__exit__(None, None, None)

    def check(self, bot, map_, n, seed=3):
        r = play_game(self.br, {0: bot}, seats(n, 0), seed, map_)
        self.assertTrue(r["winners"], r)
        self.assertEqual(r["errors"], 0, r)

    def test_example_tile2(self):
        self.check(ExampleBot(), "plaettchen", 2)

    def test_rusher_tile2(self):
        self.check(Rusher(), "plaettchen", 2)

    def test_rusher_orig4(self):
        self.check(Rusher(), "original", 4)


if __name__ == "__main__":
    unittest.main()

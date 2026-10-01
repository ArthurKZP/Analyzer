import unittest

from analyzer.theory.handclass import DRAWS, MADE, annotate, classify, labels


def made(combo, board):
    return MADE[classify(combo, tuple(board.split()))[0]][0]


def draws(combo, board):
    mask = classify(combo, tuple(board.split()))[1]
    return [key for i, (key, _) in enumerate(DRAWS) if mask & (1 << i)]


class HandClassTest(unittest.TestCase):
    def test_made_hands(self):
        board = "Kc 7h 2s"
        self.assertEqual(made("AhAd", board), "overpair")
        self.assertEqual(made("QhQd", board), "underpair")
        self.assertEqual(made("7c7d", board), "set")
        self.assertEqual(made("KdQs", board), "toppair")
        self.assertEqual(made("8s7d", board), "secondpair")
        self.assertEqual(made("5s2d", board), "weakpair")
        self.assertEqual(made("Kd7c", board), "twopair")
        self.assertEqual(made("AhQd", board), "acehigh")
        self.assertEqual(made("QhJd", board), "nothing")
        self.assertEqual(made("Jh9h", "Jc 9d 9s"), "full")
        self.assertEqual(made("Qh9d", "Jc 9d 9s"), "trips")
        self.assertEqual(made("AcQc", "Kc 7c 2c 5h 9d"), "flush")
        self.assertEqual(made("9s8d", "Tc 7h 6s"), "straight")
        # la quinte du board ne compte pas pour la main : paire servie sous la plus haute carte
        self.assertEqual(made("6d6c", "7d 8d 9s Th Js"), "underpair")
        self.assertEqual(made("KhKd", "Kc Ks 2d"), "quads")

    def test_draws(self):
        self.assertEqual(draws("8h6h", "Kc 7h 2h"), ["fd"])
        self.assertEqual(draws("9s8d", "Tc 7h 2s"), ["oesd"])
        self.assertEqual(draws("Ah5d", "4c 3h Ks"), ["gutshot"])
        self.assertEqual(draws("AsKs", "Qs Js 2d"), ["fd", "gutshot"])
        self.assertEqual(draws("Ah5h", "Kh 9c 2d"), ["bdfd"])
        self.assertEqual(draws("QhJd", "Kc 7h 2s"), ["nodraw"])
        self.assertEqual(draws("9s8d", "Tc 7h 2s 3d 4c"), [])  # river : plus de tirage

    def test_annotate(self):
        node = {"board": ["Kc", "7h", "2s"], "hands": [[["AhAd", 1.0, 0.8, 3.0]], [["KdQs", 1.0, 0.6, 2.0, 1.0, 0.0]]]}
        annotate(node)
        self.assertEqual(node["cats"], [[[MADE.index(("overpair", "Overpair")), 16]],
                                        [[MADE.index(("toppair", "Top pair")), 16]]])
        self.assertEqual(labels()["draws"][0], ["fd", "Tirage couleur"])


if __name__ == "__main__":
    unittest.main()

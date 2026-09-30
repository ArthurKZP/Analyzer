import unittest

from analyzer.cards import combo_notation, describe_holding, equity, evaluate, parse_card


def ev(cards: str):
    return evaluate([parse_card(c) for c in cards.split()])


class CardsTest(unittest.TestCase):
    def test_combo_notation(self):
        self.assertEqual(combo_notation(["Js", "Th"]), "JTo")
        self.assertEqual(combo_notation(["9h", "Ah"]), "A9s")
        self.assertEqual(combo_notation(["Kd", "Kc"]), "KK")

    def test_hand_ranking(self):
        order = [
            "Ah Kd 9c 7s 3h 2d 4c",  # hauteur
            "Ah Ad 9c 7s 3h 2d 5c",  # paire
            "Ah Ad 9c 9s 3h 2d 5c",  # double paire
            "Ah Ad As 9s 3h 2d 7c",  # brelan
            "Ah 2d 3c 4s 5h 9d Kc",  # quinte (roue)
            "Ah Jh 9h 4h 2h 2d 5c",  # couleur
            "Ah Ad As 9s 9h 2d 5c",  # full
            "Ah Ad As Ac 9h 2d 5c",  # carré
            "5h 6h 7h 8h 9h Ad Ac",  # quinte flush
        ]
        values = [ev(h) for h in order]
        self.assertEqual(values, sorted(values))
        self.assertEqual([v[0] for v in values], list(range(9)))

    def test_two_trips_make_a_full_house(self):
        self.assertEqual(ev("Ah Ad As 9s 9h 9d 5c")[:3], (6, 14, 9))

    def test_kickers_break_ties(self):
        self.assertGreater(ev("Ah Ad Kc 9s 7h 3d 2c"), ev("As Ac Qc 9s 7h 3d 2c"))
        self.assertEqual(ev("Ah Kd Qc Js Th 2d 3c"), ev("As Kc Qd Jh Ts 2h 3d"))

    def test_equity_exact_and_montecarlo(self):
        self.assertEqual(equity(["Ah", "Ad"], ["Kh", "Kd"], ["2c", "7s", "9d", "Jh", "3s"]), 1.0)
        self.assertEqual(equity(["Ah", "Kd"], ["As", "Kc"], ["2c", "7s", "9d", "Jh", "3s"]), 0.5)
        # AA vs KK sur le flop 2-7-9 : KK n'a que 2 outs (1 - 2/45 * ... ≈ 91 %)
        self.assertAlmostEqual(equity(["Ah", "Ad"], ["Kh", "Kd"], ["2c", "7s", "9d"]), 0.912, delta=0.01)
        self.assertAlmostEqual(equity(["Ah", "Ad"], ["Kh", "Kc"], []), 0.82, delta=0.015)

    def test_describe_holding(self):
        self.assertEqual(describe_holding(["Kh", "Qd"], ["Kc", "7s", "2d"]), "Top paire")
        self.assertEqual(describe_holding(["Ah", "Ad"], ["Kc", "7s", "2d"]), "Overpair")
        self.assertEqual(describe_holding(["9h", "8h"], ["Kh", "7h", "2d"]), "Rien + tirage couleur")
        self.assertEqual(describe_holding(["9h", "8c"], ["Tc", "7s", "2d"]), "Rien + tirage quinte")
        self.assertEqual(describe_holding(["Ah", "3c"], ["8c", "8s", "2d", "2h", "Kd"]), "Rien")
        self.assertEqual(describe_holding(["7h", "6c"], ["7c", "8s", "8d"]), "2e paire")
        self.assertEqual(describe_holding(["9s", "Qc"], ["Qs", "Ks", "7s", "7h", "4c"]), "2e paire")
        self.assertEqual(describe_holding(["Kh", "7c"], ["Kc", "7s", "2d"]), "Double paire")
        self.assertEqual(describe_holding(["Kh", "3c"], ["8c", "8s", "2d", "2h", "Kd"]), "Top paire")
        self.assertEqual(describe_holding(["5h", "5c"], ["Jc", "9s", "2d"]), "Petite paire servie")


if __name__ == "__main__":
    unittest.main()

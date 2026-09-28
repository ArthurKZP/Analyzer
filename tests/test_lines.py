import unittest
from pathlib import Path

from analyzer.lines import (
    Line,
    Seen,
    check_ranges,
    classify,
    holding_class,
    nonshowdown_losses,
    verdict,
    villain_lines,
)
from analyzer.parsers import load_hands

FIXTURES = Path(__file__).parent / "fixtures"


class ClassifyTest(unittest.TestCase):
    def test_intents(self):
        # Top paire : value
        self.assertEqual(classify(["Kh", "Qd"], ["7c", "7d"], ["Kc", "8s", "2d"])[0], "value")
        # Paire faible : value fine
        self.assertEqual(classify(["5h", "4d"], ["Ac", "Qd"], ["Kc", "8s", "5d"])[0], "thin")
        # Tirage couleur + quinte ouverte au turn contre top paire : ~34 % d'équité, semi-bluff
        intent, eq, _ = classify(["9s", "8s"], ["Kd", "Qc"], ["Ts", "7s", "2d", "Kh"])
        self.assertEqual(intent, "semi")
        self.assertAlmostEqual(eq, 15 / 44, places=2)
        # Tirage couleur seul : ~20 % d'équité, bluff
        self.assertEqual(classify(["9s", "8s"], ["Ah", "Kd"], ["As", "7s", "2d", "3h"])[0], "bluff")
        # À la river un tirage raté est un bluff
        self.assertEqual(classify(["9s", "8s"], ["Kd", "Qc"], ["Ts", "7s", "2d", "Kh", "3c"])[0], "bluff")

    def test_holding_class(self):
        self.assertEqual(holding_class(["Kd", "9d"], ["Kc", "7h", "2s"]), "fort")
        self.assertEqual(holding_class(["9d", "8d"], ["Kd", "7d", "2s"]), "tirage")
        self.assertEqual(holding_class(["4d", "3c"], ["Kc", "7h", "4s"]), "paire")
        self.assertEqual(holding_class(["Ad", "3c"], ["Kc", "7h", "9s", "Td", "2h"]), "rien")


class VillainLinesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = load_hands([FIXTURES])
        cls.lines = {(ln.street, ln.name): ln for ln in villain_lines(cls.hands, "Villain", "Hero")}

    def test_check_raise_flop_with_your_folded_hand(self):
        line = self.lines[("flop", "Check-raise")]
        self.assertEqual(line.count, 1)
        self.assertEqual(line.replies["fold"], 1)
        self.assertEqual(line.fold_holdings["fort"], 1)  # K9 sur K-7-2
        self.assertEqual(line.seen, [])

    def test_river_bluff_seen_at_showdown(self):
        line = self.lines[("river", "Mise après check au turn · petite (≤ 55 %)")]
        self.assertEqual(line.replies["call"], 1)
        self.assertEqual(len(line.seen), 1)
        seen = line.seen[0]
        self.assertEqual(seen.intent, "bluff")
        self.assertEqual(seen.equity, 0.0)
        self.assertAlmostEqual(line.required[0], 20 / 160)

    def test_verdicts(self):
        hand = self.hands[1]

        def line_with(street, intents, required=0.33):
            seen = [Seen(hand, i, 0.0, "", "", "call", None) for i in intents]
            return Line(street, "Test", count=len(intents), required=[required] * len(intents), seen=seen)

        self.assertEqual(verdict(line_with("river", ["value"] * 8)).kind, "value")
        river_bluffs = verdict(line_with("river", ["bluff"] * 7 + ["value"] * 3))
        self.assertEqual(river_bluffs.kind, "bluff")
        self.assertIn("Paye tes bluff-catchers", river_bluffs.decision)
        self.assertIn("Folder tes bluff-catchers est correct", verdict(line_with("river", ["value"] * 12)).decision)
        self.assertEqual(verdict(line_with("turn", ["semi", "semi", "semi", "bluff", "value"])).kind, "semi-bluff")
        self.assertEqual(verdict(line_with("river", ["bluff", "value"])).kind, "peu vue")
        self.assertEqual(verdict(Line("river", "Test", count=4)).kind, "inconnue")


class HeroSideTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = load_hands([FIXTURES])

    def test_nonshowdown_losses(self):
        data = nonshowdown_losses(self.hands, "Hero")
        self.assertEqual(data["by_street"]["flop"]["hero_folds"][0], 1)
        self.assertAlmostEqual(data["by_street"]["flop"]["hero_folds"][1], -20.5 / 5)
        self.assertEqual(data["by_street"]["preflop"]["villain_folds"][0], 1)

    def test_check_ranges(self):
        checks = check_ranges(self.hands, "Hero", "Villain")
        # Main 2 : tu checkes turn (dame servie) puis river
        self.assertEqual(checks["turn"]["holdings"]["fort"], 1)
        self.assertEqual(checks["river"]["faced"], 1)
        self.assertEqual(checks["river"]["bets"], 1)


if __name__ == "__main__":
    unittest.main()

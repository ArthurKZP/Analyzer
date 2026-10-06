import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from analyzer import bluffs
from analyzer.app.bluffs_page import build_bluffs_page
from analyzer.parsers import load_hands
from analyzer.stats import Ratio

FIXTURES = Path(__file__).parent / "fixtures"


def shown(street, intent, line="Barrel", size="grosse (56–120 %)", value="over", theory=0.25, think=None, k=[0]):
    k[0] += 1
    return bluffs.Shown("V", SimpleNamespace(hand_id=f"H{k[0]}", date=None), 3, street, line, size, value, intent,
                        "desc", theory, think)


class FeatureTest(unittest.TestCase):
    def test_feature(self):
        board = ["Ks", "7d", "2c", "Ah", "7s"]
        self.assertEqual(bluffs.feature(board, "flop"), "King high")
        self.assertEqual(bluffs.feature(board, "turn"), "over")
        self.assertEqual(bluffs.feature(board, "river"), "paired")
        self.assertIsNone(bluffs.feature(board[:3], "turn"))
        self.assertEqual(bluffs.feature_label("turn", "flush"), "Couleur possible")

    def test_compare(self):
        self.assertEqual(bluffs.compare(Ratio(20, 20), 0.5, 5), ("plus", "solide"))
        self.assertEqual(bluffs.compare(Ratio(5, 5), 0.3, 5), ("plus", "à confirmer"))  # net, mais 5 occasions
        self.assertEqual(bluffs.compare(Ratio(0, 8), 0.48, 5), ("moins", "à confirmer"))
        self.assertIsNone(bluffs.compare(Ratio(3, 10), 0.35, 5))
        self.assertIsNone(bluffs.compare(Ratio(4, 4), 0.1, 5))  # trop peu d'occasions


class FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = list(load_hands([FIXTURES]))

    def test_frequencies_and_shown(self):
        with mock.patch.object(bluffs, "solver_rates", return_value={("cbet_flop", "King high"): 0.6}):
            report = bluffs.analyze(self.hands, ["Hero"], "Villain")
        rows = {(f.family, f.spot.key, f.feature): f for f in report.freqs}
        cbet = rows[("srp", "cbet_flop", "King high")]  # HAND01 : c-bet sur K♣7♥2♠ en pot simple
        self.assertEqual((cbet.ratio.hits, cbet.ratio.opps, cbet.solver), (1, 1, 0.6))
        self.assertEqual(rows[("3bet", "cbet_turn", "brick")].ratio.opps, 1)  # turn 3♦ sur Q♣8♠2♠ : pas de barrel
        self.assertIn(("3bet", "other_river", "straight"), rows)
        # la c-bet de HAND02 (Q♥J♥ sur Q♣8♠2♠) vue à l'abattage : de la value
        self.assertEqual([(s.street, s.line, s.feature, s.intent) for s in report.shown],
                         [("flop", "C-bet", "Queen high", "value")])

        villain = bluffs.analyze(self.hands, ["Villain"], "Hero")
        river = villain.shown[0]
        self.assertEqual((river.street, river.intent, river.bluff), ("river", "bluff", True))
        self.assertAlmostEqual(river.theory, 0.125, places=3)  # équité qu'il te fallait pour payer
        page = build_bluffs_page(villain, "de Villain")
        for text in ("Ce qui ressort", "Ses mises selon la carte", "Ses mains montrées", "Ses bluffs vus à l'abattage"):
            self.assertIn(text, page)


class PatternTest(unittest.TestCase):
    def test_river_groups_against_theory(self):
        items = [shown("river", "bluff", line="Mise après check au turn") for _ in range(8)]
        items += [shown("river", "value", line="Mise après check au turn") for _ in range(2)]
        items += [shown("river", "value", line="Relance", size="", value="brick", theory=0.3) for _ in range(5)]
        groups = bluffs.shown_groups(items)
        line = next(g for g in groups if g.dimension == "line" and g.value == "Mise après check au turn")
        self.assertEqual((line.ratio.hits, line.ratio.opps, line.reference), (8, 10, 0.25))
        self.assertEqual(line.verdict, ("plus", "solide"))
        self.assertFalse(any(g.dimension == "size" and not g.value for g in groups))  # pas de tranche pour une relance
        raise_group = next(g for g in groups if g.value == "Relance")
        self.assertEqual(raise_group.verdict, ("moins", "à confirmer"))
        found = bluffs.patterns([], groups, [])
        titles = [p.title for p in found]
        self.assertIn("À la river, sa ligne « Mise après check au turn » : souvent des bluffs", titles)
        # les 10 mêmes mains forment aussi le groupe « overcard » et le groupe de taille : comptées une fois
        self.assertEqual(sum("Mise après check" in t or "overcard" in t or "grosse" in t for t in titles), 1)
        self.assertEqual(found[0].confidence, "solide")

    def test_frequency_pattern_and_timing(self):
        f = bluffs.Freq("srp", bluffs.SPOT["cbet_turn"], "over", Ratio(18, 20), solver=0.5)
        f.verdict = bluffs.compare(f.ratio, f.solver, bluffs.MIN_FREQ)
        items = [shown("turn", "bluff", think=9.0) for _ in range(3)] + [shown("turn", "value", think=2.0) for _ in range(3)]
        times = bluffs.timing(items)
        self.assertEqual(times, [{"street": "turn", "bluff": 9.0, "value": 2.0, "n": (3, 3)}])
        found = bluffs.patterns([f], bluffs.shown_groups(items), times)
        first = found[0]
        self.assertEqual(first.title, "2e barrel (SRP) : il mise bien plus que la théorie quand une overcard tombe")
        self.assertIn("90 % sur 20 occasions, contre 50 % pour le solveur", first.evidence)
        self.assertIn("3 sans main faite sur 6", first.evidence)  # ses mises montrées sur ces cartes
        self.assertTrue(any(p.source == "timing" and "plus longtemps" in p.title for p in found))


if __name__ == "__main__":
    unittest.main()

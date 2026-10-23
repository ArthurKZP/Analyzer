import unittest
from pathlib import Path
from types import SimpleNamespace

from analyzer import bluffcatch
from analyzer.app.bluffs_page import catch_html
from analyzer.parsers import load_hands

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module

FIXTURES = Path(__file__).parent / "fixtures"


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()


def bet(reply: str, intent=None, player: str = "Villain", line: str = "Barrel (a misé le turn)",
        size: str = "grosse (76–110 %)", card: str = "brick", pot: float = 10.0, amount: float = 10.0, won=None):
    """Une mise river affrontée : pot et mise en bb (une mise pot : il te faut 33 %, ses bluffs rentables dès 50 %)."""
    hand = SimpleNamespace(hand_id="X")
    return bluffcatch.Faced(player, hand, 0, line, size, card, amount / (pot + 2 * amount), amount / (pot + amount),
                            pot, amount, amount, reply, intent, won)


class FacedTest(unittest.TestCase):
    def test_fixture(self):
        """Une mise river payée, cartes vues : la ligne, la taille, ce qu'il fallait, le résultat du call."""
        items = bluffcatch.faced(load_hands([FIXTURES]), ["Villain"], "Hero")
        self.assertEqual(len(items), 1)
        x = items[0]
        self.assertEqual((x.line, x.size, x.reply, x.intent, x.won), ("Mise après check au turn", "petite (≤ 40 %)",
                                                                      "call", "bluff", True))
        self.assertAlmostEqual(x.required, 4 / 32)
        self.assertEqual(x.profit, 28.0)  # le pot et sa mise


class SpotTest(unittest.TestCase):
    def test_pay_when_he_overbluffs(self):
        items = ([bet("call", "bluff", won=True)] * 12 + [bet("call", "value", won=False)] * 8
                 + [bet("fold")] * 10)
        every = bluffcatch.spots(items)[0]
        self.assertEqual((every.dimension, len(every.items), len(every.shown), every.share), ("toutes", 30, 20, 0.6))
        self.assertEqual(every.decision, ("payer", "solide"))  # 60 % de bluffs pour 33 % qu'il te faut
        self.assertEqual(every.won, (12, 20, 12 * 20.0 - 8 * 10.0))
        self.assertAlmostEqual(every.catcher, 0.6 * 20 - 0.4 * 10)
        self.assertIn("Paie", bluffcatch.advice(every))

    def test_fold_when_he_underbluffs(self):
        items = [bet("call", "value", won=False)] * 12 + [bet("fold")] * 8
        every = bluffcatch.spots(items)[0]
        self.assertEqual(every.decision, ("coucher", "solide"))
        self.assertIn("Couche tes bluffcatchers", bluffcatch.advice(every))

    def test_defend_more_when_overfolding(self):
        """Il bluffe autant qu'il faut, mais tu couches 85 % de ses mises quand ses bluffs sont rentables dès 50 %."""
        items = ([bet("call", "bluff", won=True)] * 1 + [bet("call", "value", won=False)] * 2 + [bet("fold")] * 17
                 + [bet("fold", line="Mise après check au turn", size="petite (≤ 40 %)", amount=3)] * 10)
        found = bluffcatch.spots(items)
        every = found[0]
        self.assertEqual(every.decision, ("défendre plus", "solide"))
        self.assertIn("Défends plus", bluffcatch.advice(every))
        self.assertEqual([s.title for s in bluffcatch.criteria(found)], ["Toutes ses mises river"])
        over = [s.title for s in bluffcatch.overfolds(found)]
        self.assertIn("Mise petite (≤ 40 %)", over)
        page = catch_html(found)
        for text in ("Bluffcatch à la river", "Où tu couches trop", "Défendre plus", "Le détail"):
            self.assertIn(text, page)
        self.assertIn("leurs bluffs", catch_html(found, plural=True))
        self.assertIn("Aucune de ses mises river", catch_html([]))

    def test_few_hands(self):
        items = [bet("call", "bluff", won=True), bet("fold")]
        every = bluffcatch.spots(items)[0]
        self.assertIsNone(every.decision)
        self.assertIn("Pas assez de mises vues", bluffcatch.advice(every))
        self.assertIn("Rien de net", catch_html(bluffcatch.spots(items)))


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

from analyzer import situations
from analyzer.app.situations_page import situations_html
from analyzer.models import BET, CHECK, FOLD
from analyzer.parsers import load_hands
from tests.base import IsolatedHome
from tests.test_ring_leaks import co_vs_bb

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"


class SituationsTest(IsolatedHome):
    def test_heads_up_spots(self):
        """En heads-up : le bouton ouvre (SRP IP attaquant), la BB 3bet (P3B OOP attaquant) ; le limp et le tapis avant
        le flop ne sont pas des situations."""
        hands = {h.hand_id: h for h in load_hands([FIXTURE])}
        found = {(h, p): situations.spot_of(hands[h], p) for h in hands for p in hands[h].seats}
        self.assertEqual((found["HAND01", "Hero"].label, found["HAND01", "Hero"].pair), ("SRP IP attaquant", "BTN vs BB"))
        self.assertEqual(found["HAND01", "Villain"].label, "SRP OOP défenseur")
        self.assertEqual((found["HAND02", "Hero"].label, found["HAND02", "Hero"].pair), ("P3B OOP attaquant", "BB vs BTN"))
        self.assertEqual(found["HAND02", "Villain"].label, "P3B IP défenseur")
        self.assertIsNone(found["HAND03", "Hero"])
        self.assertIsNone(found["HAND04", "Hero"])

    def test_ring_versus_field(self):
        """Aux tables à plusieurs : tes pots par situation et par positions, face aux réguliers du field dans la même
        situation (leurs pots sans toi aussi) ; l'écart ramené à toutes tes mains."""
        post = [("flop", "BB", CHECK), ("flop", "CO", BET, 3.0), ("flop", "BB", FOLD)]
        mine = [co_vs_bb(k, "CO", ["Ah", "Kd"], post, winner="CO") for k in range(10)]
        others = [co_vs_bb(10 + k, "UTG", ["7h", "2d"], post, winner="CO") for k in range(10)]
        report = situations.build(mine + others, "Hero", ["Joueur3", "Joueur6"])
        self.assertEqual((report.hands, report.pots), (20, 10))
        self.assertEqual([c.label for c in report.categories], ["SRP IP attaquant"])
        cat = report.categories[0]
        line = report.lines[cat.category][0]
        self.assertEqual((line.pair, line.mine.hands, line.field.hands), ("CO vs BB", 10, 10))
        self.assertAlmostEqual(line.per_pot, line.field_per_pot)  # le CO régulier gagne autant que toi
        self.assertAlmostEqual(line.gap, 0.0)
        self.assertAlmostEqual(line.weight, 100 * line.per_pot * 10 / 20)  # la moitié de tes mains
        self.assertFalse(line.clear)
        self.assertEqual(report.ranked(), [])  # moins de MIN_POTS pots : rien à classer
        page = situations_html(report)
        for text in ("Tes résultats par situation", "SRP IP attaquant", 'class="pair" data-cat="0" hidden',
                     "1 position ▸", "Réguliers du field", "Échantillon encore petit"):
            self.assertIn(text, page)
        self.assertEqual(situations_html(situations.build([], "Hero", [])), "")


if __name__ == "__main__":
    unittest.main()

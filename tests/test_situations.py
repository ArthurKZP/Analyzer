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
        """En heads-up : le bouton ouvre (SRP IP PFR), la BB 3bet (3B OOP PFR) ; le limp et le tapis avant le flop ne
        sont pas des situations."""
        hands = {h.hand_id: h for h in load_hands([FIXTURE])}
        found = {(h, p): situations.spot_of(hands[h], p) for h in hands for p in hands[h].seats}
        self.assertEqual((found["HAND01", "Hero"].label, found["HAND01", "Hero"].pair),
                         ("SRP IP PFR (IP vs BB)", "BTN vs BB"))
        self.assertEqual(found["HAND01", "Villain"].label, "SRP OOP PFC (BB vs IP)")
        self.assertEqual((found["HAND02", "Hero"].label, found["HAND02", "Hero"].pair), ("3B OOP PFR", "BB vs BTN"))
        self.assertEqual(found["HAND02", "Villain"].label, "3B IP PFC")
        self.assertIsNone(found["HAND03", "Hero"])
        self.assertIsNone(found["HAND04", "Hero"])

    def test_groups(self):
        """Les situations de tes repères : les pots simples selon les positions, les autres pots simples à part ; les
        pots 4bet en position ou non."""
        spot = situations.Spot
        self.assertEqual(spot("srp", True, True, "CO", "BB").group.key, "srp_ip_pfr")
        self.assertEqual(spot("srp", True, True, "BTN", "SB").group.key, "srp_ip_pfr_autres")
        self.assertEqual(spot("srp", False, True, "SB", "BB").group.key, "srp_oop_pfr")
        self.assertEqual(spot("srp", False, True, "CO", "BTN").group.key, "srp_oop_pfr_autres")
        self.assertEqual(spot("srp", False, False, "BB", "BTN").group.key, "srp_oop_pfc")
        self.assertEqual(spot("srp", False, False, "SB", "BTN").group.key, "srp_oop_pfc_autres")
        self.assertEqual(spot("srp", True, False, "BB", "SB").group.key, "srp_ip_pfc")
        self.assertEqual(spot("srp", True, False, "BTN", "CO").group.key, "srp_ip_pfc_autres")
        self.assertEqual(spot("3bet", False, False, "SB", "BTN").group.key, "3b_oop_pfc")
        self.assertEqual(spot("4bet", True, False, "BB", "SB").group.label, "4B PFC")
        self.assertEqual(spot("4bet", False, True, "SB", "BB").group.label, "4B PFR")
        self.assertEqual(set(situations.RING_REFERENCES), {g.key for g in situations.GROUPS})

    def test_ring_versus_references_and_field(self):
        """Aux tables à plusieurs : tes pots par situation et par positions, face à ton repère et aux réguliers du field
        dans la même situation (leurs pots sans toi aussi) ; l'écart ramené à toutes tes mains."""
        post = [("flop", "BB", CHECK), ("flop", "CO", BET, 3.0), ("flop", "BB", FOLD)]
        mine = [co_vs_bb(k, "CO", ["Ah", "Kd"], post, winner="CO") for k in range(10)]
        others = [co_vs_bb(10 + k, "UTG", ["7h", "2d"], post, winner="CO") for k in range(10)]
        field = situations.build(mine + others, "Hero", ["Joueur3", "Joueur6"])
        self.assertEqual((field.hands, field.pots), (20, 10))
        self.assertEqual([x.label for x in field.groups], ["SRP IP PFR (IP vs BB)"])
        line = field.lines["srp_ip_pfr"][0]
        self.assertEqual((line.pair, line.mine.hands, line.field.hands), ("CO vs BB", 10, 10))
        self.assertAlmostEqual(line.winrate, line.field_winrate)  # le CO régulier gagne autant que toi
        self.assertAlmostEqual(line.gap, 0.0)  # sans repère : face au field
        self.assertAlmostEqual(line.weight, line.winrate * 10 / 20)  # la moitié de tes mains
        self.assertFalse(line.clear)
        report = situations.build(mine + others, "Hero", ["Joueur3", "Joueur6"], situations.RING_REFERENCES)
        top = report.groups[0]
        self.assertEqual((top.reference, top.base), (60, 60))
        self.assertAlmostEqual(top.gap, top.winrate - 60)  # face à ton repère
        self.assertAlmostEqual(top.impact, top.gap * 10 / 20)
        self.assertEqual(report.lines["srp_ip_pfr"][0].reference, 60)  # ses positions aussi
        self.assertEqual(report.ranked(), [])  # moins de MIN_POTS pots : rien à classer
        page = situations_html(report)
        for text in ("Tes résultats par situation", "SRP IP PFR (IP vs BB)", 'class="pair" data-cat="0" hidden',
                     "1 position ▸", '<th class="num">Repère</th>', "Réguliers du field", "Échantillon encore petit"):
            self.assertIn(text, page)
        self.assertNotIn('<th class="num">Repère</th>', situations_html(field))
        self.assertEqual(situations_html(situations.build([], "Hero", [])), "")


if __name__ == "__main__":
    unittest.main()

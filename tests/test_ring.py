import unittest
from pathlib import Path

from analyzer import ring
from analyzer.app.ring_page import build_ring_page
from analyzer.parsers import load_hands, parse_text

SITES = Path(__file__).parent / "sites"


def hand(name, index=0):
    return parse_text((SITES / name).read_text(encoding="utf-8"))[index]


class ReadTest(unittest.TestCase):
    def test_six_max_betclic(self):
        h = hand("betclic_6max.txt")
        # Hero au HJ, premier à parler après le fold d'UTG : il folde
        self.assertEqual(ring.read(h, "Hero"), {"open": False, "limp": False, "vpip": False, "pfr": False})
        # la BB 3bet le vol du CO, checke le flop (pas de c-bet), gagne à l'abattage
        bb = ring.read(h, "Joueur6")
        self.assertEqual((bb["fold_steal"], bb["threebet_steal"], bb["threebet"], bb["cbet_hu"]), (False, True, True, False))
        self.assertEqual((bb["wtsd"], bb["wsd"]), (True, True))
        # le CO ouvre, paie le 3bet, ne folde pas face à la c-bet (il n'y en a pas)
        co = ring.read(h, "Joueur3")
        self.assertEqual((co["open"], co["fold_3bet"], co["wtsd"], co["wsd"]), (True, False, True, False))
        self.assertNotIn("fold_cbet", co)

    def test_three_max_winamax(self):
        h = hand("winamax.txt")
        me = ring.read(h, "Hero")  # en BB contre l'open du bouton : 3bet, c-bet, perdu à l'abattage
        self.assertEqual((me["fold_steal"], me["threebet_steal"], me["cbet_hu"], me["wtsd"], me["wsd"]),
                         (False, True, True, True, False))
        btn = ring.read(h, "Incognito-a1b2c3d4")
        self.assertEqual((btn["open"], btn["fold_3bet"], btn["fold_cbet"]), (True, False, False))

    def test_limp_then_raise_is_not_a_steal(self):
        me = ring.read(hand("unibet.txt"), "Hero")  # le HJ limpe, la SB relance : pas un vol
        self.assertEqual((me["flat"], me["threebet"], me["wtsd"], me["wsd"]), (True, False, True, True))
        self.assertNotIn("fold_steal", me)


class AnalyzeTest(unittest.TestCase):
    def test_formats_positions_and_page(self):
        hands = load_hands([SITES])
        stats = ring.analyze(hands, "Hero")
        self.assertEqual([fs.table_format for fs in stats], ["6-max", "3-max"])  # le HU n'en fait pas partie
        six = stats[0]
        self.assertEqual((six.total.hands, [p.position for p in six.positions]), (2, ["HJ", "BB"]))
        self.assertEqual(six.sites, ["Betclic", "Unibet"])
        self.assertAlmostEqual(six.total.net_bb, 88.5 / 5)
        self.assertEqual(ring.ref("6-max", "open", "BTN"), (40, 50))
        self.assertIsNone(ring.ref("3-max", "vpip"))
        page = build_ring_page(stats, "Hero")
        for text in ("Préflop par position", "Après le flop", "6-max (2)", "3-max (1)", "repère 18–23&nbsp;%"):
            self.assertIn(text, page)
        self.assertIn("Aucune main", build_ring_page([], "Hero"))


if __name__ == "__main__":
    unittest.main()

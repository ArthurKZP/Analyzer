import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer.cli import detect_hero, unify_hero
from analyzer.parsers import load_hands, parse_text

SITES = Path(__file__).parent / "sites"
FIXTURES = Path(__file__).parent / "fixtures"


def nets(hand):
    return {p: round(hand.net(p), 2) for p in hand.seats}


class WinamaxTest(unittest.TestCase):
    def setUp(self):
        self.hands = parse_text((SITES / "winamax.txt").read_text(encoding="utf-8"))

    def test_three_max_with_side_pot(self):
        h = self.hands[0]
        self.assertEqual((h.site, h.hand_id, h.table_format, h.max_seats, h.sb, h.bb), ("Winamax", "10000001-001-1000000001",
                                                                                    "3-max", 3, 2.0, 4.0))
        # places anonymes : le nom vient de l'identifiant de la ligne « Player Info »
        self.assertEqual({n: s.position for n, s in h.seats.items()},
                         {"Hero": "BB", "Incognito-a1b2c3d4": "BTN", "Incognito-0f9e8d7c": "SB"})
        self.assertEqual((h.hero, h.hole_cards["Hero"], h.board[-1]), ("Hero", ["Kd", "Kh"], "2d"))
        # le pot du résumé est net du rake ; la mise non payée revient en « side pot »
        self.assertEqual((h.total_pot, h.rake), (929.99, 2.0))
        self.assertEqual(nets(h), {"Hero": -426.5, "Incognito-a1b2c3d4": 426.5, "Incognito-0f9e8d7c": -2.0})
        raise_ = next(a for a in h.actions if a.player == "Hero" and a.kind == "raise")
        self.assertEqual((raise_.amount, raise_.to, raise_.facing), (46.0, 50.0, 6.0))
        self.assertIsNone(raise_.time)  # pas d'heure par action chez Winamax

    def test_heads_up_without_showdown(self):
        h = self.hands[1]
        self.assertEqual((h.table_format, h.button, h.big_blind), ("HU", "Hero", "Villain"))
        self.assertEqual((h.uncalled["Hero"], nets(h)), (7.0, {"Hero": 10.0, "Villain": -10.0}))
        self.assertFalse(h.showdown)


class UnibetTest(unittest.TestCase):
    def test_hero_account_and_sitting_out(self):
        (h,) = parse_text((SITES / "unibet.txt").read_text(encoding="utf-8"))
        self.assertEqual((h.site, h.table_format, h.max_seats, h.hero), ("Unibet", "6-max", 6, "Hero"))
        self.assertNotIn("Absent", h.seats)  # assis mais absent : pas servi
        self.assertEqual({n: s.position for n, s in h.seats.items()},
                         {"Hero": "BB", "Villain": "HJ", "Joueur3": "CO", "Joueur5": "BTN", "Joueur6": "SB"})
        self.assertEqual(h.hole_cards, {"Hero": ["Th", "Qh"], "Villain": ["Td", "Js"]})
        self.assertEqual(nets(h), {"Hero": 88.5, "Villain": -62.5, "Joueur3": 0.0, "Joueur5": 0.0, "Joueur6": -30.0})
        self.assertEqual(h.shown_hand["Hero"], "Two pairs, Queens up")


class BetclicRingTest(unittest.TestCase):
    def test_six_max(self):
        (h,) = parse_text((SITES / "betclic_6max.txt").read_text(encoding="utf-8"))
        self.assertEqual((h.table_format, h.max_seats, h.hero, h.button), ("6-max", 6, "Hero", "Joueur4"))
        self.assertEqual([s.position for s in sorted(h.seats.values(), key=lambda s: s.seat)],
                         ["UTG", "HJ", "CO", "BTN", "SB", "BB"])  # places sans étiquette comprises
        self.assertEqual(round(sum(nets(h).values()), 2), -4.0)  # le rake
        self.assertEqual(nets(h)["Joueur6"], 197.0)

    def test_heads_up_unchanged(self):
        hands = load_hands([FIXTURES])
        self.assertTrue(hands and all(h.table_format == "HU" for h in hands))
        h = hands[0]
        self.assertEqual(sorted(s.position for s in h.seats.values()), ["BB", "BTN"])
        self.assertEqual(h.big_blind, next(n for n in h.seats if n != h.button))


class HeroAcrossSitesTest(unittest.TestCase):
    def test_unify_and_library(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name) / "mains"
        folder.mkdir()
        for name in ("winamax.txt", "unibet.txt", "betclic_6max.txt"):
            shutil.copy(SITES / name, folder / name)
        text = (SITES / "unibet.txt").read_text(encoding="utf-8").replace("Hero[Unibet", "Moi[Unibet")
        (folder / "unibet.txt").write_text(text, encoding="utf-8")
        hands = load_hands([folder])
        self.assertEqual(detect_hero(hands), "Hero")
        unify_hero(hands, "Hero")
        unibet = next(h for h in hands if h.site == "Unibet")
        self.assertIn("Hero", unibet.seats)  # le héros Unibet (« Moi ») ramené au pseudo principal
        self.assertEqual(unibet.actions[1].player, "Hero")

        from analyzer.app.library import Library
        with mock.patch.dict(os.environ, {"ANALYZER_HOME": str(Path(tmp.name) / "home")}):
            lib = Library(folder)
            try:
                self.assertEqual([h.site for h in lib.hands], ["Winamax"])  # le HU va dans l'analyse heads-up
                self.assertEqual(sorted(h.table_format for h in lib.ring), ["3-max", "6-max", "6-max"])
                page = lib.self_page("tables")  # Mon jeu > Tables à plusieurs
                self.assertIn("Préflop par position", page)
                self.assertIn("6-max (2)", page)
            finally:
                lib.solves.shutdown()


if __name__ == "__main__":
    unittest.main()

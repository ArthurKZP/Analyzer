import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer.cli import detect_hero, unify_hero
from analyzer.parsers import load_hands, parse_text

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

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


UNIBET_UNCALLED = """Unibet Hand #2000000001 - 0.50/1.00 - No Limit Hold'Em - UTC 09:20:55 2026/03/27
Table "20000001" 6-max
*** Seated players ***
Seat 1: Joueur1 (€98.50)
Seat 3: Villain (€274.76)
Seat 4: Joueur4 (€100.50)
Seat 5: Hero[Unibet_0123456789abcdef] (€101.50)
*** Blinds and button ***
Villain has the button
Joueur4 posts small blind €0.50
Hero[Unibet_0123456789abcdef] posts big blind €1
*** Hole cards ***
Dealt in Joueur4
Dealt to Hero[Unibet_0123456789abcdef] [7c 8d]
Dealt in Joueur1
Dealt in Villain
*** Preflop ***
Joueur1 folds
Villain raises €2.50 to €2.50
Joueur4 folds
Hero[Unibet_0123456789abcdef] calls €1.50
*** Flop *** [2d Kh 7s]
Hero[Unibet_0123456789abcdef] checks
Villain bets €1.29
Hero[Unibet_0123456789abcdef] calls €1.29
*** Turn *** [2d Kh 7s] [9h]
Hero[Unibet_0123456789abcdef] checks
Villain bets €10.50
Hero[Unibet_0123456789abcdef] folds
Uncalled bet returned to Villain: €10.50
Villain wins €7.57
*** Summary ***
Total pot €8.08 Rake €0.51
Seat 3: Villain: bet €14.29 and won €18.07, net result: €3.78
Seat 4: Joueur4: bet €0.50 and won €0, net result: €-0.50
Seat 5: Hero[Unibet_0123456789abcdef]: bet €3.79 and won €0, net result: €-3.79


Unibet Hand #2000000002 - 0.50/1.00 - No Limit Hold'Em - UTC 09:22:10 2026/03/27
Table "20000001" 6-max
*** Seated players ***
Seat 1: Joueur1 (€98.50)
Seat 3: Villain (€278.54)
Seat 4: Joueur4 (€100)
Seat 5: Hero[Unibet_0123456789abcdef] (€97.71)
*** Blinds and button ***
Hero[Unibet_0123456789abcdef] has the button
Joueur1 posts small blind €0.50
Villain posts big blind €1
*** Hole cards ***
Dealt in Joueur1
Dealt in Villain
Dealt in Joueur4
Dealt to Hero[Unibet_0123456789abcdef] [Ah Qd]
*** Preflop ***
Joueur4 folds
Hero[Unibet_0123456789abcdef] raises €2.50 to €2.50
Joueur1 folds
Villain calls €1.50
*** Flop *** [Kc 8h 3d]
Villain checks
Hero[Unibet_0123456789abcdef] bets €2
Villain folds
Uncalled bet returned to Hero[Unibet_0123456789abcdef]: €2
Hero[Unibet_0123456789abcdef] wins €5.25
*** Summary ***
Total pot €5.50 Rake €0.25
Seat 1: Joueur1: bet €0.50 and won €0, net result: €-0.50
Seat 3: Villain: bet €2.50 and won €0, net result: €-2.50
Seat 5: Hero[Unibet_0123456789abcdef]: bet €4.50 and won €7.25, net result: €2.75


Unibet Hand #2000000003 - 1.00/2.00 - No Limit Hold'Em - UTC 14:17:25 2026/10/07
Table "20000003" 6-max
*** Seated players ***
Seat 1: Hero[Unibet_0123456789abcdef] (€209.96)
Seat 2: Joueur2 (€207)
Seat 3: Villain (€202)
Seat 4: Arrivant (€148.85)
Seat 5: Joueur5 (€229.59)
*** Blinds and button ***
Hero[Unibet_0123456789abcdef] has the button
Arrivant posts missed small blind €1
Arrivant posts new player's blind €2
Joueur2 posts small blind €1
Villain posts big blind €2
*** Hole cards ***
Dealt in Joueur2
Dealt in Villain
Dealt in Arrivant
Dealt in Joueur5
Dealt to Hero[Unibet_0123456789abcdef] [7d Jh]
*** Preflop ***
Arrivant checks
Joueur5 folds
Hero[Unibet_0123456789abcdef] folds
Joueur2 folds
Villain raises €10 to €12
Arrivant folds
Villain wins €16
*** Summary ***
Total pot €16 Rake €0
Seat 2: Joueur2: bet €1 and won €0, net result: €-1
Seat 3: Villain: bet €12 and won €16, net result: €4
Seat 4: Arrivant: bet €3 and won €0, net result: €-3
"""

BETCLIC_NEWCOMER = """*** HEADER ***
Site: Betclic.fr
Game Mode: Cash Game
Game Type: NL Texas Hold'em
Game Name: NLHE 1/2 6 max Deep
Game ID: 01M00000000000000000000010
Hand ID: 01M00000000000000000000011
Date & Time: 2026-01-08 14:35:55 (UTC)
Table ID: 01M00000000000000000000012
Blinds: €1.00/€2.00
Total Pot: €31.00
Rake: €0.00
*** PLAYERS ***
Seat 1: Joueur1 (€246.76)
Seat 2: Joueur2 (€289.65) [BTN]
Seat 3: Joueur3 (€141.97) [SB]
Seat 4: Arrivant (€273.15) [BB]
Seat 5: Hero (€257.50) [Hero]
Seat 6: Joueur6 (€210.20)
*** HOLE CARDS ***
Hero: [Qc Th]
*** PRE-FLOP ***
14:35:55 - Arrivant: Posts SB €1.00
14:35:55 - Joueur3: Posts SB €1.00
14:35:55 - Arrivant: Posts BB €2.00
14:36:03 - Hero: Folds
14:36:03 - Joueur6: Folds
14:36:03 - Joueur1: Folds
14:36:09 - Joueur2: Raises to €5.00
14:36:09 - Joueur3: Folds
14:36:20 - Arrivant: Raises to €24.00
14:36:22 - Joueur2: Folds
*** SUMMARY ***
Arrivant wins main pot of €31.00
"""


class MoneyTest(unittest.TestCase):
    """Le résultat de chaque joueur, tel que le site le compte (Unibet l'imprime : « net result »)."""

    def test_unibet_uncalled_bet_is_not_won_twice(self):
        # le « won » du résumé compte la mise non payée rendue : le gain se lit sur « X wins »
        villain_bets, hero_bets, blinds = parse_text(UNIBET_UNCALLED)
        self.assertEqual(nets(villain_bets), {"Joueur1": 0.0, "Villain": 3.78, "Joueur4": -0.5, "Hero": -3.79})
        self.assertEqual(nets(hero_bets), {"Joueur1": -0.5, "Villain": -2.5, "Joueur4": 0.0, "Hero": 2.75})
        self.assertFalse(hero_bets.showdown)
        # blindes d'entrée : la petite manquée est morte, celle du nouveau joueur compte dans sa mise
        self.assertEqual(nets(blinds), {"Hero": 0.0, "Joueur2": -1.0, "Villain": 4.0, "Arrivant": -3.0,
                                        "Joueur5": 0.0})
        self.assertEqual((blinds.small_blind, blinds.big_blind), ("Joueur2", "Villain"))
        check = next(a for a in blinds.actions if a.player == "Arrivant" and a.kind == "check")
        self.assertEqual(check.facing, 0.0)  # sa blinde d'entrée vaut la grosse blinde : il checke

    def test_betclic_newcomer_posts_dead_small_blind(self):
        (h,) = parse_text(BETCLIC_NEWCOMER)
        self.assertEqual((h.small_blind, h.big_blind), ("Joueur3", "Arrivant"))
        self.assertEqual(nets(h), {"Joueur1": 0.0, "Joueur2": -5.0, "Joueur3": -1.0, "Arrivant": 6.0, "Hero": 0.0,
                                   "Joueur6": 0.0})

    def test_every_pot_balances(self):
        """Sur chaque main des exemples : la somme des résultats est le rake prélevé."""
        texts = [UNIBET_UNCALLED, BETCLIC_NEWCOMER] + [f.read_text(encoding="utf-8") for f in
                                                        sorted(SITES.glob("*.txt")) + sorted(FIXTURES.glob("*.txt"))]
        for text in texts:
            for h in parse_text(text):
                self.assertAlmostEqual(sum(h.net(p) for p in h.seats), -h.rake, places=2, msg=h.hand_id)


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
        with mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(Path(tmp.name) / "home")}):
            lib = Library(folder)
            try:
                self.assertEqual([h.site for h in lib.hands], ["Winamax"])  # le HU va dans l'analyse heads-up
                self.assertEqual(sorted(h.table_format for h in lib.ring), ["3-max", "6-max", "6-max"])
                page = lib.self_page("tables")  # Mon jeu > Tables à plusieurs : 3-max et 6-max ensemble
                self.assertIn("Préflop par position", page)
                self.assertIn("Toutes tes mains (3)", page)
                self.assertIn("3-max, 6-max", page)
            finally:
                lib.solves.shutdown()


if __name__ == "__main__":
    unittest.main()

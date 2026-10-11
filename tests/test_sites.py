import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer.cli import detect_hero, unify_hero
from analyzer.parsers import count_hands, load_hands, parse_text

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

    def test_cash_outs(self):
        """L'argent reçu d'une assurance (« Cash Out ») compte dans le résultat du joueur ; le pot n'est alors pas versé."""
        (h,) = parse_text(POKERSTARS_CASH_OUT)
        self.assertEqual(nets(h), {"Joueur1": 0.0, "Perdant": -4.9, "Joueur3": -0.05, "Gagnant": 4.39, "Hero": 0.0,
                                   "Joueur6": 0.0})
        (h,) = parse_text(GGPOKER_EV_CASHOUT)  # GGPoker : il perd le coup, l'assurance le paie
        self.assertEqual((nets(h)["a9b8c7d6"], nets(h)["Hero"]), (68.1, 348.16))

    def test_every_pot_balances(self):
        """Sur chaque main des exemples : la somme des résultats est le rake prélevé."""
        texts = [UNIBET_UNCALLED, BETCLIC_NEWCOMER] + [f.read_text(encoding="utf-8") for f in
                                                        sorted(SITES.glob("*.txt")) + sorted(FIXTURES.glob("*.txt"))]
        for text in texts:
            for h in parse_text(text):
                self.assertAlmostEqual(sum(h.net(p) for p in h.seats), -h.rake, places=2, msg=h.hand_id)


POKERSTARS_CASH_OUT = """PokerStars Hand #200000000010:  Hold'em No Limit ($0.02/$0.05 USD) - 2026/11/01 10:24:41 CET [2026/11/01 5:24:41 ET]
Table 'Exemple III' 6-max Seat #1 is the button
Seat 1: Joueur1 ($5 in chips)
Seat 2: Perdant ($5.14 in chips)
Seat 3: Joueur3 ($5 in chips)
Seat 4: Gagnant ($8.20 in chips)
Seat 5: Hero ($6 in chips)
Seat 6: Joueur6 ($5 in chips)
Perdant: posts small blind $0.02
Joueur3: posts big blind $0.05
*** HOLE CARDS ***
Dealt to Hero [5d Ks]
Gagnant: raises $0.10 to $0.15
Hero: folds
Joueur6: folds
Joueur1: folds
Perdant: calls $0.13
Joueur3: folds
*** FLOP *** [8h 9d Jh]
Perdant: checks
Gagnant: bets $0.20
Perdant: calls $0.20
*** TURN *** [8h 9d Jh] [Qd]
Perdant: checks
Gagnant: bets $0.54
Perdant: raises $4.25 to $4.79 and is all-in
Gagnant: calls $4.25
*** RIVER *** [8h 9d Jh Qd] [As]
*** SHOW DOWN ***
Perdant: shows [6c 6s] (a pair of Sixes)
Gagnant: shows [Th Tc] (a straight, Eight to Queen)
Perdant cashed out the hand for $0.24
Gagnant cashed out the hand for $9.53 | Cash Out Fee $0.10
*** SUMMARY ***
Total pot $10.33 | Rake $0.46
Board [8h 9d Jh Qd As]
Seat 1: Joueur1 (button) folded before Flop (didn't bet)
Seat 2: Perdant (small blind) showed [6c 6s] and lost with a pair of Sixes (cashed out).
Seat 3: Joueur3 (big blind) folded before Flop
Seat 4: Gagnant showed [Th Tc] and won ($9.87) with a straight, Eight to Queen (pot not awarded as player cashed out)
Seat 5: Hero folded before Flop (didn't bet)
Seat 6: Joueur6 folded before Flop (didn't bet)
"""

GGPOKER_EV_CASHOUT = """Poker Hand #HD100000010: Hold'em No Limit ($5/$10) - 2026/05/02 10:24:28
Table 'Exemple GG' 6-max Seat #2 is the button
Seat 1: a7b8c9d0 ($2,505.46 in chips)
Seat 2: f1e2d3c4 ($1,000 in chips)
Seat 3: a9b8c7d6 ($368.16 in chips)
Seat 4: Hero ($1,821.42 in chips)
Seat 5: b5c6d7e8 ($1,105.71 in chips)
Seat 6: c2d3e4f5 ($1,000 in chips)
a9b8c7d6: posts small blind $5
Hero: posts big blind $10
*** HOLE CARDS ***
Dealt to a7b8c9d0
Dealt to f1e2d3c4
Dealt to a9b8c7d6
Dealt to Hero [Kh 9d]
Dealt to b5c6d7e8
Dealt to c2d3e4f5
b5c6d7e8: folds
c2d3e4f5: folds
a7b8c9d0: folds
f1e2d3c4: folds
a9b8c7d6: raises $20 to $30
Hero: calls $20
*** FLOP *** [Ah 7h 6h]
a9b8c7d6: bets $60
Hero: raises $1,731.42 to $1,791.42 and is all-in
a9b8c7d6: calls $278.16 and is all-in
Uncalled bet ($1,453.26) returned to Hero
Hero: shows [Kh 9d] (Ace-High)
a9b8c7d6: shows [Td As] (Pair of Aces)
*** TURN *** [Ah 7h 6h] [Jh]
*** RIVER *** [Ah 7h 6h Jh] [Jc]
a9b8c7d6: Chooses to EV Cashout
a9b8c7d6: Receives Cashout ($436.26)
*** SHOWDOWN ***
Hero collected $716.32 from pot
*** SUMMARY ***
Total pot $736.32 | Rake $10 | Jackpot $10 | Bingo $0 | Fortune $0 | Tax $0
Board [Ah 7h 6h Jh Jc]
Seat 1: a7b8c9d0 folded before Flop (didn't bet)
Seat 2: f1e2d3c4 (button) folded before Flop (didn't bet)
Seat 3: a9b8c7d6 (small blind) showed [Td As] and lost with Pair of Aces and Pair of Jacks, EV Cashout ($436.26)
Seat 4: Hero (big blind) showed [Kh 9d] and won ($716.32) with Ace High Flush
Seat 5: b5c6d7e8 folded before Flop (didn't bet)
Seat 6: c2d3e4f5 folded before Flop (didn't bet)
"""


def acts(hand, player):
    return [(a.kind, a.amount, a.to, a.facing) for a in hand.actions if a.player == player]


class PokerStarsFamilyTest(unittest.TestCase):
    """PokerStars, GGPoker et HHPoker : le même format d'historique."""

    def test_pokerstars(self):
        returning, both_blinds, zoom = parse_text((SITES / "pokerstars.txt").read_text(encoding="utf-8"))
        h = returning
        self.assertEqual((h.site, h.hand_id, h.table_id, h.max_seats, h.sb, h.bb, h.hero),
                         ("PokerStars", "200000000001", "Exemple I", 6, 0.02, 0.05, "Hero"))
        self.assertNotIn("Absent", h.seats)  # assis mais absent (listé en premier) : pas servi
        self.assertEqual({n: s.position for n, s in h.seats.items()},
                         {"Hero": "SB", "Joueur2": "BB", "Joueur3": "HJ", "Revenant": "CO", "Joueur6": "BTN"})
        # le joueur qui revient poste une petite blinde morte : elle ne compte pas dans sa relance
        self.assertEqual(acts(h, "Revenant")[:2], [("post_ante", 0.02, 0.0, 0.05), ("raise", 0.15, 0.15, 0.05)])
        self.assertEqual(nets(h), {"Hero": -0.02, "Joueur2": -0.05, "Joueur3": -0.3, "Revenant": 0.33, "Joueur6": 0.0})
        self.assertTrue(h.showdown)
        self.assertEqual((h.hole_cards["Joueur3"], h.shown_hand["Revenant"]), (["Qs", "Kd"], "a pair of Nines"))
        # « small & big blinds » : la grosse blinde compte dans sa mise, la petite est morte
        self.assertEqual(acts(both_blinds, "Revenant")[0], ("post_ante", 0.07, 0.05, 0.05))
        self.assertEqual(acts(both_blinds, "Villain")[0], ("raise", 0.2, 0.2, 0.05))
        self.assertEqual(nets(both_blinds)["Revenant"], -0.07)
        # une main Zoom se lit comme les autres
        self.assertEqual((zoom.site, zoom.hero, zoom.position("Hero"), zoom.uncalled["Hero"]),
                         ("PokerStars", "Hero", "BTN", 0.18))
        self.assertEqual((nets(zoom)["Hero"], zoom.showdown), (0.28, False))

    def test_ggpoker(self):
        missed, twice = parse_text((SITES / "ggpoker.txt").read_text(encoding="utf-8"))
        self.assertEqual((missed.site, missed.hero, missed.rake), ("GGPoker", "Hero", 23.0))
        # blinde manquée (morte) puis grosse blinde de retour (vivante) : la vraie grosse blinde reste celle de la main
        self.assertEqual(acts(missed, "a3b4c5d6"), [("post_ante", 25.0, 0.0, 50.0), ("post_ante", 50.0, 50.0, 50.0),
                                                    ("call", 130.0, 180.0, 130.0), ("check", 0.0, 0.0, 0.0),
                                                    ("check", 0.0, 0.0, 0.0), ("check", 0.0, 0.0, 0.0)])
        self.assertEqual((missed.small_blind, missed.big_blind), ("b4c5d6e7", "c5d6e7f8"))
        self.assertEqual(nets(missed)["a3b4c5d6"], 232.0)
        # GGPoker sert tout le monde (« Dealt to X » sans cartes) : le héros est celui dont on voit les cartes
        self.assertEqual(twice.hole_cards["Hero"], ["4h", "8d"])
        # une main jouée deux fois : le premier tableau, les gains additionnés ; le jackpot compte avec le rake
        self.assertEqual(twice.board, ["3h", "Ad", "Qs", "Tc", "7s"])
        self.assertEqual((twice.winnings, twice.rake), ({"a1b2c3d4": 442.38}, 8.0))
        self.assertTrue(next(a for a in twice.actions if a.player == "a1b2c3d4" and a.kind == "raise" and a.all_in))

    def test_hhpoker_antes_and_dead_button(self):
        (h,) = parse_text((SITES / "hhpoker.txt").read_text(encoding="utf-8"))
        self.assertEqual((h.site, h.hero, h.size), ("HHPoker", "Hero", 6))
        self.assertNotIn("Parti", h.seats)
        # le bouton est une place vide : le joueur juste avant fait le bouton
        self.assertEqual({n: s.position for n, s in h.seats.items()},
                         {"Hero": "BTN", "Joueur3": "SB", "Joueur4": "BB", "Joueur5": "UTG", "Villain": "HJ",
                          "Joueur7": "CO"})
        self.assertEqual(acts(h, "Hero"), [("post_ante", 1.0, 0.0, 0.0), ("fold", 0.0, 0.0, 30.0)])
        self.assertEqual(nets(h), {"Hero": -1.0, "Joueur3": -6.0, "Joueur4": 58.72, "Joueur5": -1.0,
                                   "Villain": -54.43, "Joueur7": -1.0})


class OtherSitesTest(unittest.TestCase):
    def test_wpn(self):
        text = (SITES / "wpn.txt").read_text(encoding="utf-8")
        late, dead_button, twice = parse_text(text)  # le bomb pot est écarté
        self.assertEqual(count_hands(text), 4)
        # une place listée sans tapis (« will be allowed to play… ») n'empêche pas de lire les suivantes
        self.assertEqual(set(late.seats), {"Joueur1", "Hero", "Villain", "Joueur6"})
        self.assertEqual(acts(late, "Joueur6"), [("fold", 0.0, 0.0, 20.0)])
        self.assertEqual(acts(late, "Hero"), [("post_sb", 10.0, 10.0, 0.0), ("raise", 50.0, 60.0, 10.0)])
        self.assertEqual((late.winnings, late.uncalled["Hero"], nets(late)["Hero"]), ({"Hero": 40.0}, 40.0, 20.0))
        # bouton absent : celui qui parle en dernier avant les blindes ; le rake compte les frais du jackpot
        self.assertEqual((dead_button.button, dead_button.rake, nets(dead_button)["Villain"]), ("Joueur4", 2.25, 22.75))
        self.assertNotIn("Joueur1", dead_button.seats)
        self.assertEqual(twice.board, ["3s", "6s", "Qh", "Qd", "Jc"])
        self.assertEqual(nets(twice), {"Joueur1": -20.0, "Joueur2": -1335.28, "Hero": 0.0, "Villain": 1362.28,
                                       "Joueur6": -10.0})

    def test_ipoker(self):
        heads_up, three = parse_text((SITES / "ipoker.txt").read_text(encoding="utf-8"))  # la table anonyme est écartée
        h = heads_up
        self.assertEqual((h.site, h.table_format, h.button, h.hero), ("iPoker", "HU", "Hero", "Hero"))
        self.assertEqual(h.hole_cards, {"Hero": ["Jd", "Tc"], "Villain": ["As", "Ah"]})  # « DJ C10 » : J♦ 10♣
        self.assertEqual(h.board, ["6d", "9d", "6s", "5c", "3d"])
        self.assertEqual(acts(h, "Hero")[1], ("raise", 40.0, 50.0, 10.0))  # « Raise X » : relance à X
        self.assertEqual(acts(h, "Villain")[-1], ("call", 1362.97, 1362.97, 1999.03))  # tapis pour moins : un suivi
        self.assertEqual((h.total_pot, h.rake, h.uncalled["Hero"]), (4245.94, 1.5, 636.06))
        self.assertEqual(nets(h), {"Villain": 2121.47, "Hero": -2122.97})
        # « Allin X » : ce que le joueur ajoute (ici une relance)
        self.assertEqual(acts(three, "Hero")[1], ("raise", 149.0, 150.0, 5.0))
        self.assertEqual(nets(three), {"Joueur1": 149.0, "Hero": -150.0, "Villain": -2.0})

    def test_partypoker(self):
        returning, capped = parse_text((SITES / "partypoker.txt").read_text(encoding="utf-8"))
        h = returning
        self.assertEqual((h.site, h.hand_id, h.table_id, h.max_seats, h.sb, h.bb), ("partypoker", "600000001", "Exemple",
                                                                                   6, 2.0, 4.0))
        # « big blind + dead [6 €] » : 4 vivants (il checke), 2 morts
        self.assertEqual(acts(h, "Revenant")[:2], [("post_ante", 6.0, 4.0, 4.0), ("check", 0.0, 4.0, 0.0)])
        self.assertEqual(acts(h, "Hero")[1], ("raise", 14.0, 16.0, 2.0))  # « raises [X] » : ce qu'il ajoute
        # tout le monde s'est couché : la relance non payée sort des gains, le pot et le rake s'en déduisent
        self.assertEqual((h.total_pot, h.rake, h.uncalled["Revenant"], h.winnings["Revenant"]),
                         (96.66, 4.0, 69.99, 92.66))
        self.assertEqual(nets(h), {"Revenant": 51.33, "Hero": -39.33, "Joueur6": 0.0, "Villain": -16.0,
                                   "Joueur2": 0.0, "Joueur3": 0.0})
        # l'en-tête donne la cave (« €25 EUR NL ») : les blindes viennent des blindes postées
        self.assertEqual((capped.sb, capped.bb, capped.rake, capped.showdown), (0.1, 0.25, 2.0, True))
        self.assertEqual(acts(capped, "Hero")[-1], ("raise", 19.43, 19.43, 8.38))
        self.assertEqual(nets(capped), {"Joueur1": 0.0, "Villain": 24.16, "Hero": -26.16})


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

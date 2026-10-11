import unittest
from pathlib import Path

from analyzer.models import CALL, RAISE
from analyzer.parsers import load_hands, parse_text

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

FIXTURES = Path(__file__).parent / "fixtures"


class BetclicParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = {h.hand_id: h for h in load_hands([FIXTURES])}

    def test_loads_all_hands_in_chronological_order(self):
        ordered = load_hands([FIXTURES])
        self.assertEqual([h.hand_id for h in ordered], ["HAND01", "HAND02", "HAND03", "HAND04"])

    def test_positions_and_hero(self):
        h = self.hands["HAND01"]
        self.assertEqual(h.hero, "Hero")
        self.assertEqual(h.button, "Hero")
        self.assertEqual(h.big_blind, "Villain")
        self.assertEqual((h.sb, h.bb), (2.5, 5.0))

    def test_amounts_are_increments(self):
        h = self.hands["HAND02"]
        call = next(a for a in h.actions if a.kind == CALL and a.street == "preflop")
        self.assertEqual((call.amount, call.to), (30.0, 40.0))
        three_bet = next(a for a in h.actions if a.kind == RAISE and a.player == "Hero")
        self.assertEqual((three_bet.amount, three_bet.to), (35.0, 40.0))

    def test_every_pot_balances(self):
        for h in self.hands.values():
            invested = sum(h.put.values()) - sum(h.uncalled.values())
            self.assertAlmostEqual(invested, h.total_pot, places=2, msg=h.hand_id)
            self.assertAlmostEqual(sum(h.winnings.values()) + h.rake, h.total_pot, places=2, msg=h.hand_id)
            self.assertAlmostEqual(sum(h.net(p) for p in h.seats), -h.rake, places=2, msg=h.hand_id)

    def test_uncalled_excess_of_allin_is_returned(self):
        h = self.hands["HAND03"]
        self.assertEqual(h.uncalled["Villain"], 300.0)
        self.assertAlmostEqual(h.net("Villain"), 497.5)
        self.assertAlmostEqual(h.net("Hero"), -500.0)

    def test_showdown_cards_and_board(self):
        h = self.hands["HAND02"]
        self.assertTrue(h.showdown)
        self.assertEqual(h.hole_cards["Villain"], ["Ts", "9s"])
        self.assertEqual(h.board, ["Qc", "8s", "2s", "3d", "4h"])
        self.assertEqual(h.shown_hand["Villain"], "High Card")

    def test_showdown_from_actions(self):
        """L'abattage, comme dans PokerTracker : au moins deux joueurs vont au bout sans se coucher ; la ligne
        « Showdown » de l'historique ne compte pas (absente d'un format, ou écrite quand un joueur montre ses cartes
        après le fold de l'autre)."""
        text = (FIXTURES / "betclic_sample.txt").read_text(encoding="utf-8")
        hands = {h.hand_id: h for h in parse_text(text.replace("*** SHOWDOWN ***\n", ""))}
        self.assertTrue(hands["HAND02"].showdown)  # rivière jouée jusqu'au bout, sans la ligne
        self.assertTrue(hands["HAND03"].showdown)  # tapis payé
        self.assertFalse(hands["HAND04"].showdown)
        shown = text.replace("*** SUMMARY ***\nHero wins main pot of €25.00",
                             "*** SHOWDOWN ***\nHero shows [7c 2d] (High Card)\n*** SUMMARY ***\nHero wins main pot of €25.00")
        self.assertNotEqual(shown, text)
        self.assertFalse({h.hand_id: h for h in parse_text(shown)}["HAND04"].showdown)  # il a foldé : pas d'abattage

    def test_non_actions_are_ignored(self):
        h = self.hands["HAND01"]
        self.assertEqual([a.kind for a in h.actions][-1], "fold")

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_text("Partie n° 1 : Hold'em No Limit")
        self.assertEqual(parse_text("PokerStars Hand #1: Hold'em No Limit"), [])  # un format connu, sans main lisible

    def test_duplicates_are_removed(self):
        hands = load_hands([FIXTURES, FIXTURES / "betclic_sample.txt"])
        self.assertEqual(len(hands), 4)


if __name__ == "__main__":
    unittest.main()

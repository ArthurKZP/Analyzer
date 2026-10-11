import unittest
from pathlib import Path

from analyzer.insights import findings
from analyzer.parsers import load_hands
from analyzer.stats import HandReader, allin_ev, analyze

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

FIXTURES = Path(__file__).parent / "fixtures"


class StatsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = load_hands([FIXTURES])
        cls.by_id = {h.hand_id: h for h in cls.hands}
        cls.stats = analyze(cls.hands)
        cls.v, cls.h = cls.stats["Villain"], cls.stats["Hero"]

    def ratio(self, ps, key):
        r = ps.r(key)
        return r.hits, r.opps

    def test_preflop_situations(self):
        self.assertEqual(self.ratio(self.v, "sb_first.call"), (1, 2))  # limp
        self.assertEqual(self.ratio(self.v, "sb_first.raise"), (1, 2))
        self.assertEqual(self.ratio(self.v, "bb_vs_open.raise"), (1, 2))  # 3bet
        self.assertEqual(self.ratio(self.v, "sb_vs_3bet.call"), (1, 1))
        self.assertEqual(self.ratio(self.v, "bb_vs_4bet.raise"), (1, 1))  # 5bet
        self.assertEqual(self.ratio(self.v, "sb_vs_iso.fold"), (1, 1))
        self.assertEqual(self.ratio(self.h, "bb_vs_limp.raise"), (1, 1))
        self.assertEqual(self.ratio(self.h, "sb_vs_3bet.raise"), (1, 1))  # 4bet

    def test_vpip_pfr(self):
        self.assertEqual(self.ratio(self.v, "vpip"), (4, 4))
        self.assertEqual(self.ratio(self.v, "pfr"), (2, 4))
        self.assertEqual(self.ratio(self.h, "pfr"), (4, 4))

    def test_postflop_situations(self):
        self.assertEqual(self.ratio(self.h, "cbet_flop_ip"), (1, 1))
        self.assertEqual(self.ratio(self.h, "cbet_flop_oop"), (1, 1))
        self.assertEqual(self.ratio(self.h, "cbet_turn"), (0, 1))
        self.assertEqual(self.ratio(self.v, "xr_flop"), (1, 1))
        self.assertEqual(self.ratio(self.v, "vs_cbet_flop.raise"), (1, 2))
        self.assertEqual(self.ratio(self.h, "vs_raise_flop.fold"), (1, 1))
        self.assertEqual(self.ratio(self.v, "float_turn"), (0, 1))
        self.assertEqual(self.ratio(self.h, "vs_bet_river.call"), (1, 1))

    def test_preflop_line_and_pot_type(self):
        reader = HandReader(self.by_id["HAND02"])
        self.assertEqual(reader.preflop_line("Villain"), "open → call 3bet")
        self.assertEqual(reader.preflop_line("Hero"), "3bet")
        self.assertEqual(reader.pot_type, "3bp")
        self.assertEqual(reader.pfa, "Hero")

    def test_showdown_stats(self):
        self.assertEqual(self.ratio(self.v, "wtsd"), (2, 3))
        self.assertEqual(self.ratio(self.v, "wsd"), (1, 2))

    def test_results_and_allin_ev(self):
        self.assertAlmostEqual(self.h.net, -20.5 + 77 - 500 + 5, places=2)
        self.assertAlmostEqual(self.h.net + self.v.net, -(1.5 + 3 + 2.5), places=2)
        ev = allin_ev(self.by_id["HAND03"])
        # AA contre KK préflop : ~82 % d'équité sur un pot net de 997,50
        self.assertAlmostEqual((ev["Hero"] + 500) / 997.5, 0.82, delta=0.015)
        self.assertEqual(self.h.allin_hands, 1)
        self.assertEqual(len(self.h.curve), 4)

    def test_sizes(self):
        self.assertEqual(self.v.sizes["pf_open_bb"], [2.0])
        self.assertEqual(self.h.sizes["pf_3bet_x"], [4.0])
        self.assertAlmostEqual(self.v.sizes["bet_river"][0], 100 * 20 / 120)  # 20 € dans un pot de 120 €

    def test_findings_need_sample(self):
        self.assertEqual(findings(self.v), [])


if __name__ == "__main__":
    unittest.main()

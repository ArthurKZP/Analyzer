import unittest
from pathlib import Path

from analyzer.lines import Line, Seen
from analyzer.parsers import load_hands
from analyzer.plan import PHASES, _facing, _preflop, bluff_results, build_plan, plan_text
from analyzer.stats import PlayerStats, Ratio, analyze

FIXTURES = Path(__file__).parent / "fixtures"


def player(name, **ratios):
    ps = PlayerStats(name)
    for key, (hits, opps) in ratios.items():
        ps.ratios[key.replace("__", ".")] = Ratio(hits, opps)
    return ps


class PreflopRulesTest(unittest.TestCase):
    def test_defend_against_frequent_3bets(self):
        v = player("V", bb_vs_open__raise=(30, 100))
        h = player("H", sb_vs_3bet__fold=(20, 30))
        items = _preflop(v, h)
        self.assertEqual(len(items), 1)
        self.assertIn("défends beaucoup plus", items[0].action)
        self.assertEqual(items[0].confidence, "solide")

    def test_sticky_and_wide_defender(self):
        v = player("V", sb_vs_3bet__fold=(5, 30), bb_vs_open__fold=(10, 100), bb_vs_4bet__fold=(0, 4))
        actions = " ".join(it.action for it in _preflop(v, player("H")))
        self.assertIn("3bet pour la value", actions)
        self.assertIn("n'ouvre pas pour voler", actions)
        self.assertIn("uniquement pour la value", actions)

    def test_no_rule_without_sample(self):
        v = player("V", bb_vs_open__raise=(5, 8))
        self.assertEqual(_preflop(v, player("H", sb_vs_3bet__fold=(5, 6))), [])


class PostflopRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = load_hands([FIXTURES])

    def test_bluff_results(self):
        flop = bluff_results(self.hands, "Hero", "Villain")["flop"]["all"]
        self.assertEqual((flop["n"], flop["folds"]), (2, 0))
        # Un bluff pur perd sa mise quand il paie ou relance : -8/25 puis -20/80
        self.assertAlmostEqual(flop["result"], -8 / 25 - 20 / 80)

    def test_value_raises_and_semi_bluff_barrels(self):
        hand = self.hands[0]
        raises = Line("turn", "Relance", "", count=6,
                      seen=[Seen(hand, "value", 0.9, "", "", "call", None) for _ in range(6)])
        barrels = Line("turn", "Barrel", "grosse (56–120 %)", count=5,
                       seen=[Seen(hand, "semi", 0.35, "", "", "call", None) for _ in range(4)]
                       + [Seen(hand, "value", 0.8, "", "", "call", None)])
        actions = [it.action for it in _facing(self.hands, "Hero", "Villain", [raises, barrels])]
        self.assertTrue(any(a.startswith("Ses relances turn : de la value") for a in actions))
        self.assertTrue(any(a.startswith("Ses grosses mises turn : surtout de gros tirages") for a in actions))


class BuildPlanTest(unittest.TestCase):
    def test_plan_on_fixture(self):
        hands = load_hands([FIXTURES])
        plan = build_plan(hands, analyze(hands), "Hero", "Villain")
        self.assertTrue(plan.profile)
        self.assertEqual(list(plan.by_phase()), list(PHASES))
        self.assertTrue(plan_text(plan).startswith("PLAN DE JEU"))


if __name__ == "__main__":
    unittest.main()

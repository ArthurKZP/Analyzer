import unittest
from pathlib import Path

from analyzer.lines import Line, Seen
from analyzer.parsers import load_hands
from analyzer.plan import PHASES, _facing, _preflop, bluff_results, build_plan, plan_text
from analyzer.stats import PlayerStats, Ratio, analyze
from analyzer.theory.preflop import Decision, load_solution, summarize

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

FIXTURES = Path(__file__).parent / "fixtures"


def player(name, **ratios):
    ps = PlayerStats(name)
    for key, (hits, opps) in ratios.items():
        ps.ratios[key.replace("__", ".")] = Ratio(hits, opps)
    return ps


def summaries(*plays):
    """Synthèses préflop à partir de (nœud, main, action jouée, nombre de fois)."""
    solution = load_solution()
    out = []
    for node, combo, action, count in plays:
        strategy = solution.nodes[node].strategy(combo)
        out += [Decision(None, "H", node, combo, action, None, strategy, 100.0) for _ in range(count)]
    return summarize(out, solution)


class PreflopRulesTest(unittest.TestCase):
    def test_trash_opens_are_flagged(self):
        v = player("V", bb_vs_open__fold=(20, 100))
        items = _preflop(v, player("H"), summaries(("sb_open", "72o", "raise", 5), ("sb_open", "83o", "raise", 4)))
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].action.startswith("Au bouton, jette ces mains que le solveur n'ouvre jamais"))
        self.assertIn("72o, 83o", items[0].action)
        self.assertIn("ces opens ne volent rien", items[0].why)
        self.assertEqual(items[0].confidence, "solide")

    def test_deviation_allowed_as_exploit(self):
        v = player("V", bb_vs_open__fold=(60, 100))  # il abandonne sa BB : ouvrir large est un exploit
        actions = [it.action for it in _preflop(v, player("H"), summaries(("sb_open", "72o", "raise", 9)))]
        self.assertFalse(any("jette ces mains" in a for a in actions))
        self.assertTrue(any(a.startswith("Ouvre très large") for a in actions))

    def test_rare_deviation_is_ignored(self):
        self.assertEqual(_preflop(player("V"), player("H"), summaries(("bb_vs_open", "J4o", "raise", 3))), [])

    def test_defend_more_against_3bets(self):
        plays = summaries(("sb_vs_3bet", "KQs", "fold", 12), ("sb_vs_3bet", "J4o", "fold", 10))
        items = _preflop(player("V", bb_vs_open__raise=(30, 100)), player("H"), plays)
        defense = [it for it in items if it.action.startswith("Face à ses 3bets, défends plus")]
        self.assertEqual(len(defense), 1)
        self.assertIn("le solveur folde 45 % ; toi 100 % (22/22)", defense[0].why)
        self.assertIn("Face au 3bet, continue avec ces mains : KQs.", [it.action for it in items])

    def test_no_defense_advice_when_folds_match_solver(self):
        plays = summaries(("sb_vs_3bet", "KQs", "call", 12), ("sb_vs_3bet", "J4o", "fold", 10))
        self.assertEqual(_preflop(player("V", bb_vs_open__raise=(30, 100)), player("H"), plays), [])

    def test_sticky_and_wide_defender(self):
        v = player("V", sb_vs_3bet__fold=(5, 30), bb_vs_open__fold=(10, 100), bb_vs_4bet__fold=(0, 4))
        actions = " ".join(it.action for it in _preflop(v, player("H"), summaries()))
        self.assertIn("3bet pour la value", actions)
        self.assertIn("n'ouvre pas pour voler", actions)
        self.assertIn("uniquement pour la value", actions)

    def test_no_rule_without_sample(self):
        v = player("V", bb_vs_open__raise=(5, 8), sb_vs_3bet__fold=(1, 6))
        self.assertEqual(_preflop(v, player("H"), summaries(("sb_vs_3bet", "KQs", "fold", 3))), [])


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

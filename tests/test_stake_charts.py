import json
import unittest
from pathlib import Path

from analyzer import leaks
from analyzer.cards import combo_notation
from analyzer.parsers import load_hands
from analyzer.theory import preflop, stake_charts

try:
    from .base import IsolatedHome
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome

FIXTURES = Path(__file__).parent / "fixtures"


def reference_ranges() -> dict:
    """La solution fournie, réécrite en ranges action par action (comme un import)."""
    base = json.loads(Path(preflop.DEFAULT_SOLUTION).read_text(encoding="utf-8"))
    nodes = {}
    for key, node in base["nodes"].items():
        actions: dict[str, list[str]] = {}
        for hand, freqs in node["hands"].items():
            total = sum(freqs.values())
            for action, w in freqs.items():
                if action != "fold" and w > 0:
                    actions.setdefault(action, []).append(f"{hand}:{w / total:.4f}")
        nodes[key] = {action: ",".join(items) for action, items in actions.items()}
    return {"format": "HU", "source": "Essai", "rake": "5 %", "nodes": nodes}


class RangesTest(unittest.TestCase):
    def test_round_trip(self):
        """Des ranges action par action aux fréquences de chaque nœud, sur les combos qui y arrivent."""
        base = json.loads(Path(preflop.DEFAULT_SOLUTION).read_text(encoding="utf-8"))
        built = stake_charts.hu_from_ranges(reference_ranges())
        for key in ("sb_open", "bb_vs_open", "sb_vs_3bet"):
            measured = base["nodes"][key]["measured"]
            for action, value in built["nodes"][key]["totals"].items():
                self.assertAlmostEqual(value, measured[action], delta=0.3, msg=(key, action))
        self.assertEqual(built["nodes"]["sb_vs_3bet"]["parent"], ["sb_open", "raise"])
        # un nœud absent garde celui de la référence ; une main hors des ranges folde
        only = stake_charts.hu_from_ranges({"nodes": {"sb_open": {"raise": "AA,KK"}}})
        self.assertEqual(only["nodes"]["sb_open"]["hands"]["72o"], {"fold": 1.0})
        self.assertEqual(only["nodes"]["sb_open"]["totals"]["raise"], round(100 * 12 / 1326, 1))
        self.assertEqual(set(only["nodes"]["sb_vs_3bet"]["hands"]), {"AA", "KK"})  # seules les mains ouvertes


class StoreTest(IsolatedHome):
    def test_save_and_validate(self):
        self.assertEqual(stake_charts.available(), {})
        self.assertEqual(stake_charts.save("HU", "hautes", reference_ranges())["size"], 4)
        self.assertEqual(stake_charts.summary("HU", "hautes")["rake"], "5 %")
        lines = {"lines": {"CO:raise BB:call": {"pot_type": "SRP", "ranges": {"CO": "AA", "BB": "KK"}}}}
        stake_charts.save("6-max", "milieu", lines)
        self.assertEqual(stake_charts.available(), {"6-max": ["milieu"], "HU": ["hautes"]})
        for fmt, band, data in (("HU", "enorme", reference_ranges()), ("7-card", "micro", lines),
                                ("HU", "micro", {"nodes": {"sb_open": {"raise": "AXo"}}}),
                                ("HU", "micro", {"nodes": {"sb_open": {"raise": "AA", "call": "AA"}}}),
                                ("HU", "micro", {"nodes": {"sb_open": {"bet": "AA"}}}),
                                ("6-max", "micro", {"lines": {}}), ("HU", "micro", ["AA"])):
            with self.assertRaises(ValueError, msg=(fmt, band, data)):
                stake_charts.save(fmt, band, data)
        self.assertTrue(stake_charts.delete("6-max", "milieu"))
        self.assertEqual(stake_charts.available(), {"HU": ["hautes"]})

    def test_hands_judged_at_their_stakes(self):
        """Chaque main est jugée avec les charts de son niveau de limites ; les autres, avec ceux de la référence."""
        hands = load_hands([FIXTURES])  # grosse blinde à 5 : hautes limites
        hand = next(h for h in hands if h.button == "Hero")
        combo = combo_notation(hand.hole_cards["Hero"])
        before = stake_charts.theory().strategy(hand, "Hero", "open", [], combo)
        self.assertEqual(before, preflop.load_solution().nodes["sb_open"].strategy(combo))
        stake_charts.save("HU", "micro", {"nodes": {"sb_open": {"raise": "AA"}}})  # pas son niveau
        self.assertEqual(stake_charts.theory().strategy(hand, "Hero", "open", [], combo), before)
        stake_charts.save("HU", "hautes", {"nodes": {"sb_open": {"raise": combo}}})
        self.assertEqual(stake_charts.theory().strategy(hand, "Hero", "open", [], combo), {"raise": 1.0})
        # les repères : la théorie de chaque niveau, pondérée par ses mains de chaque niveau
        share = stake_charts.hu_solution("hautes").nodes["sb_open"].total("raise")
        self.assertEqual(stake_charts.hu_value("sb_open", "raise", {"hautes": 3}), share)
        mixed = stake_charts.hu_value("sb_open", "raise", {"hautes": 1, "milieu": 1})
        self.assertAlmostEqual(mixed, (share + preflop.gto_value("sb_open", "raise")) / 2)
        self.assertEqual(stake_charts.mix(hands), {"hautes": len(hands)})
        self.assertIn("hautes limites (4 mains)", stake_charts.used("HU", hands))
        self.assertEqual(stake_charts.page_band("HU", hands)[0], "hautes")
        stats = leaks.stats_for(hands, "Hero", {})
        opens = next(s for s in stats if s.key == "sb_first.raise")
        self.assertAlmostEqual(opens.reference, share / 100)

    def test_no_charts(self):
        self.assertEqual(stake_charts.used("HU", load_hands([FIXTURES])), "")
        self.assertEqual(stake_charts.page_band("HU", []), (None, ""))
        self.assertIs(stake_charts.hu_solution("hautes"), preflop.load_solution())


if __name__ == "__main__":
    unittest.main()

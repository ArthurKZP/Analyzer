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


SUITS = "cdhs"


def combos(hand: str, weight: float = 1) -> str:
    """Les combos d'une main au format texte de GTO Wizard (« AcKd: 1 »)."""
    a, b = hand[0], hand[1]
    if a == b:
        pairs = [(a + s1, b + s2) for i, s1 in enumerate(SUITS) for s2 in SUITS[i + 1:]]
    elif hand.endswith("s"):
        pairs = [(a + s1, b + s1) for s1 in SUITS]
    else:
        pairs = [(a + s1, b + s2) for s1 in SUITS for s2 in SUITS if s1 != s2]
    return ",".join(f"{x}{y}: {weight:g}" for x, y in pairs)


OPENS = """Open raise 2.5x, essai
UTG
{utg}

BU
{btn}
""".format(utg=combos("AA") + "," + combos("AKs"), btn=combos("AA") + "," + combos("72o", 0.5))

DEFENSE = """Ranges de défense, essai
Size ip : 7.5bb

VS OPEN 2.5x UTG

HJ
{hj}

BB (3b et call)
\t3b :
{bb3}
\tCall :
{bbc}
""".format(hj=combos("AA") + "," + combos("KK"), bb3=combos("AA") + "," + combos("AKs", 0.5),
           bbc=combos("AKs", 0.5) + "," + combos("22"))


VS3BET = """Ranges de défense (4bet et call) vs 3bet, essai
Sizings de 4b oop :
\tUTG / HJ versus ip : 20bb
\tUTG vs bb : 22bb

VS HJ 3BET
Utg
\t4b :
{four}
\tcall :
{call}
""".format(four=combos("AA"), call=combos("AKs", 0.5))


class TextTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(stake_charts.parse_text(OPENS), {"open:UTG": "AA,AKs", "open:BTN": "AA,72o:0.5"})
        self.assertEqual(stake_charts.parse_text(DEFENSE), {"3bet:UTG:HJ": "AA,KK", "3bet:UTG:BB": "AA,AKs:0.5",
                                                            "call:UTG:BB": "AKs:0.5,22"})
        self.assertEqual(stake_charts.class_range("AcKd: 1, AhKh: 0.5"), "AKo:0.0833,AKs:0.125")
        lines = stake_charts.lines_from_parts({**stake_charts.parse_text(OPENS), **stake_charts.parse_text(DEFENSE)})
        self.assertEqual(lines["UTG:raise BB:call"], {"pot_type": "SRP", "ranges": {"UTG": "AA,AKs",
                                                                                    "BB": "AKs:0.5,22"}})
        self.assertEqual(lines["UTG:raise HJ:raise UTG:call"]["ranges"], {"HJ": "AA,KK"})
        for bad in (combos("AA"),  # une range sans titre de position
                    "VS OPEN 2.5x UTG\nBB\n" + combos("AA"),  # la BB : 3b ou call ?
                    "VS OPEN 2.5x\nHJ\n" + combos("AA"),  # face à quel open ?
                    "rien du tout"):
            with self.assertRaises(ValueError, msg=bad[:40]):
                stake_charts.parse_text(bad)
        everything = "UTG\n" + ",".join(combos(a + b + ("" if a == b else k)) for i, a in enumerate("AKQJT98765432")
                                         for b in "AKQJT98765432"[i:] for k in (("",) if a == b else ("s", "o")))
        with self.assertRaises(ValueError) as found:  # toute la range de départ, pas l'open
            stake_charts.parse_text(everything)
        self.assertIn("100 %", str(found.exception))
        with self.assertRaises(ValueError):  # 3bet et call d'une même main : plus de 100 %
            stake_charts.lines_from_parts({"3bet:UTG:BB": "AA", "call:UTG:BB": "AA"})

    def test_vs_3bet(self):
        """La réponse de l'ouvreur au 3bet : ses calls (pot 3bet) et ses 4bets (pot 4bet), poids de toute sa range."""
        self.assertEqual(stake_charts.parse_text(VS3BET), {"4bet:UTG:HJ": "AA", "call3:UTG:HJ": "AKs:0.5"})
        lines = stake_charts.lines_from_parts({**stake_charts.parse_text(OPENS), **stake_charts.parse_text(DEFENSE),
                                               **stake_charts.parse_text(VS3BET)})
        self.assertEqual(lines["UTG:raise HJ:raise UTG:call"],
                         {"pot_type": "pot 3bet", "ranges": {"HJ": "AA,KK", "UTG": "AKs:0.5"}})
        self.assertEqual(lines["UTG:raise HJ:raise UTG:raise HJ:call"],
                         {"pot_type": "pot 4bet", "ranges": {"UTG": "AA"}})
        with self.assertRaises(ValueError):  # il paie et 4bette plus de mains qu'il n'en ouvre
            stake_charts.lines_from_parts({"open:UTG": "AA", "call3:UTG:HJ": "AA:0.6", "4bet:UTG:HJ": "AA:0.6"})
        with self.assertRaises(ValueError):  # 4b ou call ?
            stake_charts.parse_text("VS HJ 3BET\nUtg\n" + combos("AA"))


class StoreTest(IsolatedHome):
    def test_text_import_completes_reference(self):
        """L'export texte se complète fichier par fichier ; ce qu'il ne donne pas reste celui de la référence."""
        from analyzer.handplay import _chart_open, _chart_vs_open
        from analyzer.theory import ring_ranges
        ring_ranges.save_solution("6-max", {"lines": {
            "UTG:raise BB:call": {"pot_type": "SRP", "ranges": {"UTG": "AA,KK,QQ", "BB": "QQ,JJ"}},
            "UTG:raise BB:raise UTG:call": {"pot_type": "pot 3bet", "ranges": {"BB": "KK", "UTG": "QQ"}}}})
        summary = stake_charts.import_text(DEFENSE, "hautes")
        self.assertEqual(summary["covers"], "pas d'open (ceux de la référence) ; défense face à l'open : UTG ; "
                                            "pas de réponse au 3bet")
        lines = stake_charts.ring_lines("hautes")["6-max"]
        self.assertEqual(_chart_open(lines, "UTG", "KK"), {"raise": 1.0, "fold": 0.0})  # l'open de la référence
        self.assertEqual(_chart_vs_open(lines, "UTG", "BB", "22"), {"raise": 0.0, "call": 1.0, "fold": 0.0})
        reference = stake_charts.ring_lines("milieu")["6-max"]  # un autre niveau : la référence
        self.assertEqual(reference["UTG:raise BB:call"]["BB"], {"QQ": 1.0, "JJ": 1.0})
        # l'open arrive : il remplace celui de la référence ; sa réponse au 3bet (faite pour un autre open)
        # n'est plus lue
        path = self.home.parent / "opens.txt"
        path.write_text(OPENS, encoding="utf-8")
        self.assertEqual(stake_charts.import_file(path, "hautes"), "6-max")
        lines = stake_charts.ring_lines("hautes")["6-max"]
        self.assertEqual(_chart_open(lines, "UTG", "KK"), {"raise": 0.0, "fold": 1.0})
        self.assertNotIn("UTG", lines["UTG:raise BB:raise UTG:call"])
        self.assertIn("opens : UTG, BTN", stake_charts.summary("6-max", "hautes")["covers"])
        stake_charts.import_text(VS3BET, "hautes")  # la réponse au 3bet arrive : elle est lue
        self.assertIn("réponse au 3bet : UTG", stake_charts.summary("6-max", "hautes")["covers"])
        self.assertEqual(stake_charts.ring_lines("hautes")["6-max"]["UTG:raise BB:raise UTG:call"].get("UTG"), None)
        self.assertEqual(stake_charts.ring_lines("hautes")["6-max"]["UTG:raise HJ:raise UTG:call"]["UTG"],
                         {"AKs": 0.5})
        self.assertEqual(stake_charts.summary("6-max", "hautes")["source"],
                         "Ranges de défense, essai · Open raise 2.5x, essai · Ranges de défense (4bet et call) vs "
                         "3bet, essai")
        with self.assertRaises(ValueError):
            stake_charts.import_text(OPENS, "hautes", "HU")

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

"""Mon préflop aux tables à plusieurs : tes décisions rangées en nœuds (situation, positions) face aux charts, avec les
mêmes cartes ; une table sans charts à elle prend ceux du 6-max à même nombre de joueurs derrière."""
import shutil
import unittest
from pathlib import Path

from analyzer.models import CALL, FOLD, RAISE
from analyzer.theory import ring_preflop, ring_ranges
from analyzer.theory.page import build_ring_preflop_page
from analyzer.theory.preflop import DEVIATION, MAIN, OUT_OF_RANGE
from tests.base import IsolatedHome
from tests.test_ring_leaks import FOLDS_TO, LINES, POSITIONS, table_hand

SITES = Path(__file__).parent / "sites"
NINE = ("UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN", "SB", "BB")


def charts():
    ring_ranges.save_solution("6-max", {"source": "Charts de test — synthétiques",
                                        "lines": {k: {"ranges": v} for k, v in LINES.items()}})


def co_open_vs_bb_3bet(n, cards, answer):
    """Le joueur ouvre au CO, la BB relance ; answer : sa réponse (action, montant)."""
    return table_hand(n, "CO", cards, FOLDS_TO["CO"] + [("CO", RAISE, 5.0), ("BTN", FOLD), ("SB", FOLD),
                                                         ("BB", RAISE, 20.0), ("CO", *answer)])


class CollectTest(IsolatedHome):
    def test_nodes_and_verdicts(self):
        charts()
        hands = [table_hand(n, "UTG", ["7h", "2d"], [("UTG", RAISE, 5.0)] + [(p, FOLD) for p in POSITIONS[1:]])
                 for n in range(3)]
        hands.append(co_open_vs_bb_3bet(3, ["Ah", "Kh"], (CALL,)))        # AKs : ouvre et paie le 3bet
        hands.append(co_open_vs_bb_3bet(4, ["7c", "2s"], (FOLD,)))        # 72o : ouvre, puis se couche
        hands.append(co_open_vs_bb_3bet(5, ["Ah", "Kd"], (RAISE, 150.0)))  # AKo : hors de ses charts d'open
        found = ring_preflop.collect(hands, "Hero")
        self.assertEqual((found.hands, found.charts), (6, ["6-max"]))
        nodes = found.solution.nodes
        self.assertEqual(list(nodes), ["open|6-max|UTG", "open|6-max|CO", "vs_3bet|6-max|CO|BB"])
        threebet = nodes["vs_3bet|6-max|CO|BB"]
        self.assertEqual((threebet.label, threebet.word("raise"), threebet.parent),
                         ("CO face au 3bet de la BB", "4bet", ("open|6-max|CO", "raise")))
        # la part d'une main qui arrive face au 3bet : sa fréquence d'open
        self.assertEqual((found.solution.weight(threebet.key, "AKs"), found.solution.weight(threebet.key, "AKo")),
                         (1.0, 0.0))
        verdicts = {(d.node, d.combo): (d.action, d.verdict) for d in found.decisions}
        self.assertEqual(verdicts[("open|6-max|UTG", "72o")], ("raise", DEVIATION))
        self.assertEqual(verdicts[("vs_3bet|6-max|CO|BB", "AKs")], ("call", MAIN))
        self.assertEqual(verdicts[("vs_3bet|6-max|CO|BB", "72o")], ("fold", MAIN))
        self.assertEqual(verdicts[("vs_3bet|6-max|CO|BB", "AKo")], ("raise", OUT_OF_RANGE))  # le tapis : une relance
        self.assertEqual(nodes["open|6-max|UTG"].totals, {"raise": 1.4, "fold": 98.6})  # 18 combos sur 1326

    def test_nine_handed_table_uses_six_max_charts(self):
        """À 9 joueurs, le LJ ouvre comme l'UTG du 6-max ; l'UTG d'une table pleine n'a pas de chart."""
        charts()
        lj = [table_hand(n, "LJ", ["Kh", "Kd"], [(p, FOLD) for p in NINE[:3]] + [("LJ", RAISE, 5.0)] + [
            (p, FOLD) for p in NINE[4:]], positions=NINE) for n in range(4)]
        utg = [table_hand(n, "UTG", ["Kh", "Kd"], [("UTG", RAISE, 5.0)] + [(p, FOLD) for p in NINE[1:]],
                          positions=NINE) for n in range(4, 6)]
        six = [table_hand(n, "UTG", ["Ah", "Ad"], [("UTG", RAISE, 5.0)] + [(p, FOLD) for p in POSITIONS[1:]])
               for n in range(6, 8)]
        self.assertEqual(lj[0].table_format, "9 joueurs")
        found = ring_preflop.collect(lj + utg + six, "Hero")
        node = found.solution.nodes["open|6-max|UTG"]
        self.assertEqual(node.seen, {("9 joueurs", "LJ"): 4, ("6-max", "UTG"): 2})
        self.assertEqual(len(found.decisions), 6)  # l'UTG à 9 joueurs n'est pas comparé
        self.assertTrue(all(d.verdict == MAIN for d in found.decisions))
        page = build_ring_preflop_page(found, "Hero")
        self.assertIn("Tes décisions jugées avec ces charts : LJ en 9 joueurs (4), UTG en 6-max (2).", page)

    def test_limp_and_page(self):
        charts()
        limps = [table_hand(n, "UTG", ["7h", "2d"], [("UTG", CALL)] + [(p, FOLD) for p in POSITIONS[1:5]] + [
            ("BB", "check")]) for n in range(16)]
        found = ring_preflop.collect(limps, "Hero")
        node = found.solution.nodes["open|6-max|UTG"]
        self.assertEqual((node.actions, node.word("call")), (["raise", "call", "fold"], "limp"))
        page = build_ring_preflop_page(found, "Hero", switch='<nav class="lk-fmt"></nav>')
        for text in ("UTG, premier à parler", "Premier à parler", "Charts,<br>mêmes mains", "Limp au lieu de fold",
                     "Action principale des charts", 'class="lk-fmt"',
                     "<b>UTG, premier à parler</b> <span class=\"muted\">· 16 décisions</span>",
                     "Le leak</span><span>Trop de limp : 100&nbsp;% de tes décisions ici, contre 0&nbsp;% pour les "
                     "charts avec les mêmes mains. Surtout : limp au lieu de fold avec 72o (16 fois).</span>",
                     "Le correctif</span><span>Moins de limp, vers 0&nbsp;% ici : commence par 72o (fold plutôt que "
                     "limp).</span>", "Comment c'est comparé"):
            self.assertIn(text, page)

    def test_without_charts(self):
        hands = [table_hand(1, "UTG", ["7h", "2d"], [("UTG", RAISE, 5.0)] + [(p, FOLD) for p in POSITIONS[1:]])]
        found = ring_preflop.collect(hands, "Hero")
        self.assertEqual((found.decisions, found.charts), ([], []))
        self.assertIn("Pas encore de charts", build_ring_preflop_page(found, "Hero", load_hint=" Charge-les."))


class AppTest(IsolatedHome):
    def library(self, names):
        folder = self.home.parent / "mains"
        folder.mkdir()
        for name in names:
            shutil.copy(SITES / name, folder / name)
        from analyzer.app.library import Library
        return Library(folder)

    def test_preflop_tab_switches_format(self):
        charts()
        lib = self.library(("betclic_6max.txt", "unibet.txt", "winamax.txt"))  # Winamax : aussi une main heads-up
        heads_up = lib.self_page("preflop")
        self.assertIn('href="?format=ring"', heads_up)
        ring = lib.self_page("preflop", table_format="ring")
        self.assertIn("Tables à plusieurs · charts 6-max", ring)
        self.assertIn('aria-current="page">Tables à plusieurs', ring)
        self.assertEqual(lib.self_page("preflop", table_format="6-max"), ring)  # l'ancien nom mène au même endroit

    def test_only_ring_hands(self):
        lib = self.library(("betclic_6max.txt", "unibet.txt"))
        page = lib.self_page("preflop")  # pas de heads-up : les tables à plusieurs directement
        self.assertIn("Pas encore de charts", page)
        self.assertNotIn('class="lk-fmt"', page)


if __name__ == "__main__":
    unittest.main()

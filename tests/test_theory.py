import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from analyzer.parsers import load_hands
from analyzer.stats import analyze
from analyzer.theory.extract import combos, extract_grid, hand_at, main, totals
from analyzer.theory.page import build_preflop_page
from analyzer.theory.preflop import (
    DEVIATION,
    MAIN,
    MIXED,
    OUT_OF_RANGE,
    Decision,
    decisions,
    gto_ref,
    gto_value,
    load_solution,
    summarize,
)

FIXTURES = Path(__file__).parent / "fixtures"

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - Pillow n'est requis que pour l'extracteur
    Image = None


class SolutionTest(unittest.TestCase):
    def test_hand_grid(self):
        self.assertEqual((hand_at(0, 0), hand_at(0, 1), hand_at(1, 0), hand_at(12, 12)), ("AA", "AKs", "AKo", "22"))
        self.assertEqual((combos("AA"), combos("AKs"), combos("AKo")), (6, 4, 12))
        all_hands = {hand_at(i, j) for i in range(13) for j in range(13)}
        self.assertEqual(sum(combos(h) for h in all_hands), 1326)

    def test_nodes_and_weights(self):
        solution = load_solution()
        self.assertEqual(list(solution.nodes), ["sb_open", "bb_vs_open", "sb_vs_3bet", "bb_vs_4bet"])
        self.assertEqual(solution.weight("sb_open", "72o"), 1.0)
        self.assertEqual(solution.weight("sb_vs_3bet", "72o"), 0.0)  # jamais ouverte, jamais 3bettée
        self.assertIsNone(solution.nodes["sb_vs_3bet"].strategy("72o"))
        self.assertEqual(solution.nodes["bb_vs_open"].word("raise"), "3bet")
        # La range qui arrive à chaque nœud reproduit les totaux affichés par le solveur (lecture à ~2 %).
        for key in solution.nodes:
            node = solution.nodes[key]
            grid = {h: dict(f, w=solution.weight(key, h)) for h, f in node.hands.items()}
            measured = totals(grid)
            for action in node.actions:
                self.assertAlmostEqual(measured[action], node.total(action), delta=2.5, msg=f"{key} {action}")
        self.assertAlmostEqual(totals({h: dict(f, w=solution.weight("sb_vs_3bet", h))
                                       for h, f in solution.nodes["sb_vs_3bet"].hands.items()})["combos"], 1090.6, delta=1)

    def test_references(self):
        self.assertEqual(gto_value("sb_open", "raise"), 82.2)
        self.assertAlmostEqual(gto_value("bb_vs_open", "vpip"), 72.7)
        self.assertEqual(gto_ref("sb_open", "raise"), (77.2, 87.2))
        self.assertEqual(gto_ref("sb_vs_3bet", "raise"), (5.3, 11.3))  # 4bet + tapis, tolérance ±3 sous 15 %
        self.assertEqual(gto_ref("bb_vs_open", "fold", tolerance=1), (26.3, 28.3))


class DecisionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = load_hands([FIXTURES])

    def test_hero_decisions(self):
        got = [(d.hand.hand_id, d.node, d.combo, d.action, d.verdict) for d in decisions(self.hands, "Hero")]
        self.assertEqual(got, [
            ("HAND01", "sb_open", "K9s", "raise", MAIN),
            ("HAND02", "bb_vs_open", "QJs", "raise", MAIN),
            ("HAND03", "sb_open", "AA", "raise", MAIN),
            ("HAND03", "sb_vs_3bet", "AA", "allin", DEVIATION),  # le solveur 4bet petit, il ne pousse pas
        ])

    def test_villain_shown_hands(self):
        got = [(d.node, d.combo, d.action, d.size_bb) for d in decisions(self.hands, "Villain")]
        self.assertEqual(got, [("sb_open", "T9s", "raise", 2.0), ("sb_vs_3bet", "T9s", "call", None),
                               ("bb_vs_open", "KK", "raise", 10.0), ("bb_vs_4bet", "KK", "allin", 160.0)])

    def test_verdicts_and_summary(self):
        solution = load_solution()
        node = solution.nodes["bb_vs_open"]

        def decision(combo, action):
            return Decision(None, "H", "bb_vs_open", combo, action, None, node.strategy(combo), 100.0)

        self.assertEqual(decision("T9s", "call").verdict, MIXED)  # 3bet 72 %, call 28 %
        self.assertEqual(decision("T9s", "raise").verdict, MAIN)
        self.assertEqual(decision("72o", "call").verdict, DEVIATION)
        out = Decision(None, "H", "sb_vs_3bet", "72o", "fold", None, solution.nodes["sb_vs_3bet"].strategy("72o"), 100.0)
        self.assertEqual(out.verdict, OUT_OF_RANGE)

        s = next(x for x in summarize([decision("72o", "call"), decision("72o", "call"), decision("AA", "raise"), out],
                                      solution) if x.node.key == "bb_vs_open")
        self.assertEqual(len(s.in_range), 3)
        self.assertAlmostEqual(s.actual["call"], 200 / 3)
        self.assertAlmostEqual(s.expected["fold"], 200 / 3)
        [((action, best), group)] = s.deviations()
        self.assertEqual((action, best, len(group)), ("call", "fold", 2))


class PageTest(unittest.TestCase):
    def test_page(self):
        hands = load_hands([FIXTURES])
        html = build_preflop_page(hands, "Hero", "Villain", analyze(hands), spots_href="v-spots.html")
        self.assertIn("<h1>Préflop vs solveur — Villain</h1>", html)
        self.assertIn("Lui face au solveur", html)
        self.assertIn('href="v-spots.html#hand=HAND03"', html)  # l'écart AA se rejoue
        self.assertIn("Bouton face au 3bet", html)
        embedded = build_preflop_page(hands, "Hero", embed=True)
        self.assertNotIn("<h1>", embedded)
        self.assertNotIn("Lui face au solveur", embedded)


@unittest.skipIf(Image is None, "Pillow absent")
class ExtractTest(unittest.TestCase):
    CELL_W, CELL_H, TOP = 84, 56, 40

    def draw(self, cells):
        """Grille 13 × 13 synthétique : {main: (poids, [(action, part), ...])}."""
        from analyzer.theory.extract import COLORS
        w, h = self.CELL_W * 13 + 1, self.TOP + self.CELL_H * 13 + 1
        image = Image.new("RGB", (w, h), (60, 60, 60))
        d = ImageDraw.Draw(image)
        for i in range(14):
            d.line([(i * self.CELL_W, 30), (i * self.CELL_W, h)], fill=(0, 0, 0))
            d.line([(0, self.TOP + i * self.CELL_H), (w, self.TOP + i * self.CELL_H)], fill=(0, 0, 0))
        for i in range(13):
            for j in range(13):
                weight, bands = cells.get(hand_at(i, j), (0, []))
                x0, y1 = j * self.CELL_W + 1, self.TOP + (i + 1) * self.CELL_H - 1
                top = y1 - round(weight * (self.CELL_H - 2))
                for action, share in bands:
                    width = round(share * (self.CELL_W - 1))
                    if width:
                        d.rectangle([x0, top, x0 + width - 1, y1 - 1], fill=COLORS[action])
                    x0 += width
        return image

    def test_extract(self):
        image = self.draw({"AA": (1, [("raise", 1.0)]), "AKs": (1, [("raise", 0.5), ("call", 0.5)]),
                           "72o": (1, [("fold", 1.0)]), "KK": (0.5, [("allin", 0.25), ("call", 0.75)])})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grille.png"
            image.save(path)
            grid = extract_grid(path)
            self.assertEqual(len(grid), 169)
            self.assertEqual(grid["AA"], {"w": 1.0, "raise": 1.0})
            self.assertAlmostEqual(grid["AKs"]["raise"], 0.5, delta=0.03)
            self.assertAlmostEqual(grid["AKs"]["call"], 0.5, delta=0.03)
            self.assertAlmostEqual(grid["KK"]["w"], 0.5, delta=0.05)
            self.assertAlmostEqual(grid["KK"]["allin"], 0.25, delta=0.03)
            self.assertEqual(grid["32o"], {"w": 0.0})

            solution = Path(tmp) / "solution.json"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(path), "--solution", str(solution), "--noeud", "test"]), 0)
            node = json.loads(solution.read_text(encoding="utf-8"))["nodes"]["test"]
            self.assertEqual(sorted(node["hands"]), ["72o", "AA", "AKs", "KK"])
            self.assertAlmostEqual(node["measured"]["combos"], 6 + 4 + 12 + 3, delta=0.5)


if __name__ == "__main__":
    unittest.main()

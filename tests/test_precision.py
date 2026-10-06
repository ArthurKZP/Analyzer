"""Précision visée des résolutions (réglable dans l'application), résultats retrouvés à toute précision, durée
estimée ; et le graphique des résultats aux courbes masquables."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from analyzer.app.solves import SolveQueue
from analyzer.parsers import load_hands
from analyzer.theory import postflop

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_SOLVER = FIXTURES / "fake_solver.py"


class HomeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"ANALYZER_HOME": tmp.name, "ANALYZER_SOLVER": str(FAKE_SOLVER)})
        env.start()
        self.addCleanup(env.stop)
        self.spot = postflop.build_spot({h.hand_id: h for h in load_hands([FIXTURES])}["HAND02"], "Hero")


class SettingTest(HomeTest):
    def test_setting_and_keys(self):
        self.assertEqual(postflop.precision(), postflop.DEFAULT_TARGET)
        self.assertEqual(postflop.set_precision(0.5), 0.5)
        self.assertEqual(postflop.precision(), 0.5)
        with self.assertRaises(ValueError):
            postflop.set_precision(0.7)
        self.assertEqual((postflop.iterations_for(0.5), postflop.iterations_for(3.0)), (600, 120))
        a, b = self.spot.request(120, 1.5), self.spot.request(600, 0.5, threads=4)
        self.assertNotEqual(postflop.study_key(a), postflop.study_key(b))
        self.assertEqual(postflop.base_key(a), postflop.base_key(b))  # le même spot

    def test_solved_request(self):
        default, fine = self.spot.request(), self.spot.request(600, 0.5)
        self.assertIsNone(postflop.solved_request(fine))
        postflop._save_cache(default, {"decisions": []})
        self.assertEqual(postflop.solved_request(fine)["target_exploit_pct"], 1.5)  # résolu, précision par défaut
        postflop._save_cache(fine, {"decisions": []})
        self.assertEqual(postflop.solved_request(default)["target_exploit_pct"], 0.5)  # la dernière précision
        postflop._save_cache(default, {"decisions": []})  # de retour à la précision par défaut : la note s'efface
        self.assertEqual(postflop.solved_request(fine)["target_exploit_pct"], 1.5)
        self.assertEqual(json.loads((self.home / "precisions.json").read_text()), {})


class EstimateTest(HomeTest):
    def test_iterations_to(self):
        curve = [[10, 8.0], [20, 4.0], [40, 2.0]]
        self.assertAlmostEqual(postflop._iterations_to(curve, 4.0), 20)
        self.assertAlmostEqual(postflop._iterations_to(curve, 3.0), 20 * (4 / 3), places=3)  # interpolée
        self.assertAlmostEqual(postflop._iterations_to(curve, 1.0), 80, places=3)  # prolongée (loi de puissance)
        self.assertEqual(postflop._iterations_to(curve, 9.0), 10)

    def test_estimate(self):
        size = {"tree_nodes": 300_000, "hands": [800, 900], "arena_mb": 900.0}
        with mock.patch.object(postflop, "tree_size", return_value=size):
            first = postflop.estimate(self.spot.request())
            self.assertFalse(first["measured"])  # repères par défaut
            by = {o["target"]: o for o in first["options"]}
            self.assertLess(by[3.0]["seconds"], by[1.5]["seconds"])
            self.assertLess(by[1.5]["seconds"], by[0.5]["seconds"])
            self.assertEqual(by[1.5]["iterations"] % 10, 0)  # l'exploitabilité se mesure toutes les 10 itérations
            # trois résolutions faites ici : la vitesse de cet ordinateur et ses courbes
            for _ in range(3):
                tracker = postflop._Tracker(None)
                tracker({"tree_nodes": 100_000, "arena_mb": 1.0, "hands": [500, 500]})
                for it, ex in ((10, 6.0), (20, 3.0), (30, 1.4)):
                    tracker({"iteration": it, "exploit_pct": ex, "elapsed": it * 0.5})
                tracker.record()
            second = postflop.estimate(self.spot.request())
        self.assertTrue(second["measured"])
        by = {o["target"]: o for o in second["options"]}
        self.assertEqual(by[1.5]["iterations"], 30)
        speed = 0.5 / (100_000 * 1000)  # s par itération, par nœud et par combo
        self.assertEqual(by[1.5]["seconds"], round(postflop.START_SECONDS + speed * 300_000 * 1700 * 30))


@unittest.skipIf(os.name == "nt", "faux solveur : script exécutable POSIX")
class RefineTest(HomeTest):
    def wait(self, queue, view):
        deadline = time.time() + 20
        while queue.get(view["job"])["state"] in ("waiting", "running") and time.time() < deadline:
            time.sleep(0.05)
        return queue.get(view["job"])

    def test_refine(self):
        queue = SolveQueue()
        self.addCleanup(queue.shutdown)
        self.assertEqual(self.wait(queue, queue.start(self.spot))["target"], 1.5)
        postflop.set_precision(0.5)
        view = queue.lookup(self.spot)
        self.assertEqual((view["state"], view["target"]), ("done", 1.5))  # déjà résolu : on le garde
        self.assertEqual(queue.start(self.spot)["state"], "done")  # rien à relancer
        fine = queue.start(self.spot, fresh=True)  # affiner à la précision réglée
        self.assertEqual(fine["target"], 0.5)
        self.assertEqual(self.wait(queue, fine)["state"], "done")
        self.assertEqual(queue.lookup(self.spot)["target"], 0.5)  # le résultat le plus récent
        self.assertEqual(len(postflop.list_studies()), 2)  # l'ancienne étude reste sur le disque


class ChartTest(unittest.TestCase):
    def test_legend_and_chart(self):
        from analyzer.report import SCRIPT, chart_svg, legend
        curve = [(0.0, 0.0, 0.0, 0.0), (5.0, 3.0, 8.0, -3.0), (2.0, 4.0, 6.0, -4.0)]
        html = legend(curve)
        self.assertEqual(html.count('type="checkbox" checked'), 4)
        svg = chart_svg(curve)
        self.assertIn('<g class="yaxis">', svg)
        self.assertEqual(svg.count("data-series="), 4)
        self.assertIn('data-y0="16"', svg)
        self.assertIn("analyzer.courbes-masquees", SCRIPT)


if __name__ == "__main__":
    unittest.main()

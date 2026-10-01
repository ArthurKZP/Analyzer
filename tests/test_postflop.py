import copy
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from analyzer.app.solves import SolveQueue
from analyzer.parsers import load_hands
from analyzer.theory import postflop
from analyzer.theory.preflop import load_solution

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_SOLVER = FIXTURES / "fake_solver.py"
POSIX = os.name != "nt"  # le faux solveur est un script exécutable


class SpotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = {h.hand_id: h for h in load_hands([FIXTURES])}

    def test_srp(self):
        spot = postflop.build_spot(self.hands["HAND01"], "Hero")
        self.assertEqual((spot.pot_type, spot.oop, spot.ip), ("SRP", "Villain", "Hero"))
        self.assertEqual((spot.pot_bb, spot.stack_bb, spot.board), (5.0, 97.5, ["Kc", "7h", "2s"]))
        self.assertEqual(spot.line, [{"action": "check"}, {"action": "bet", "to": 1.6},
                                     {"action": "raise", "to": 6.0}, {"action": "fold"}])
        self.assertEqual(spot.action_index, [4, 5, 6, 7])
        # Les tailles jouées remplacent la taille par défaut proche (33 % -> 32 %, relance 60 % -> 53,7 %).
        self.assertEqual(spot.sizes["Hero"][0]["bet"], [32.0])
        self.assertEqual(spot.sizes["Villain"][0]["raise"], [53.7])
        self.assertEqual(spot.sizes["Hero"][2]["raise"], [])  # pas de relance river par défaut
        request = spot.request(50, 2.0)
        tree = request["spot"]["tree"]
        self.assertEqual(request["combos"], {"Kd9d": 1})
        self.assertEqual(request["spot"]["board"], "Kc7h2s")
        self.assertEqual((tree["starting_pot"], tree["effective_stack"], tree["max_raises"]), (5.0, 97.5, 2))
        self.assertEqual(tree["ip"][0]["bet"], [{"PotPct": 32.0}])
        self.assertIn("AA", request["spot"]["range_ip"].split(","))
        self.assertNotIn("72o", request["spot"]["range_ip"])
        self.assertEqual((request["max_iterations"], request["target_exploit_pct"]), (50, 2.0))
        self.assertEqual(spot.menu_text().split(" · ")[0], "flop : mise 32 % / 33 %, relance 53,7 % / 60 %")

    def test_3bet_pot_with_cards(self):
        spot = postflop.build_spot(self.hands["HAND02"], "Hero")
        self.assertEqual((spot.pot_type, spot.oop, spot.ip), ("pot 3bet", "Hero", "Villain"))
        self.assertEqual(spot.combos(), {"QhJh": 0, "Ts9s": 1})
        self.assertEqual([s.get("card") for s in spot.line if "card" in s], ["3d", "4h"])
        self.assertEqual(spot.action_index.count(-1), 2)
        self.assertEqual(spot.sizes["Villain"][2]["bet"], [75.0, 16.7])  # petite mise river jouée

    def test_unsupported(self):
        with self.assertRaisesRegex(postflop.Unsupported, "Tapis préflop"):
            postflop.build_spot(self.hands["HAND03"], "Hero")
        with self.assertRaisesRegex(postflop.Unsupported, "avant le flop"):
            postflop.build_spot(self.hands["HAND04"], "Hero")

    def test_hand_outside_range_is_added(self):
        hand = copy.deepcopy(self.hands["HAND01"])
        hand.hole_cards["Hero"] = ["7c", "2d"]
        spot = postflop.build_spot(hand, "Hero")
        self.assertEqual(spot.added, ["Hero"])
        self.assertEqual(spot.ranges["Hero"]["72o"], postflop.MIN_WEIGHT)

    def test_range_weights(self):
        solution = load_solution()
        opens = postflop.range_weights(solution, "sb_open", "raise")
        calls = postflop.range_weights(solution, "bb_vs_open", "call")
        self.assertEqual(opens["AA"], 1.0)
        self.assertNotIn("72o", opens)
        self.assertAlmostEqual(calls["T9s"], 0.284, places=3)
        self.assertEqual(postflop.range_text({"AA": 1.0, "T9s": 0.284}), "AA,T9s:0.284")

    def test_merge_sizes(self):
        self.assertEqual(postflop._merge_sizes([33.0], [28.0]), [28.0])
        self.assertEqual(postflop._merge_sizes([33.0], [80.0, "a"]), [33.0, 80.0, "a"])
        self.assertEqual(postflop._merge_sizes([], []), [])


class InstallTest(unittest.TestCase):
    def test_files_read_at_compile_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "crates" / "solver" / "src" / "preflop"
            src.mkdir(parents=True)
            (root / "crates" / "solver" / "Cargo.toml").write_text("[package]\n")
            (src / "a.rs").write_text('include_str!("../../../../research/x/meta.json");\n'
                                      'include_str!(\n    "../../../../cache/contextual/m.json"\n);\n'
                                      'include_str!("kernels.cu");\n')
            self.assertEqual(postflop.external_files(root), ["cache/contextual/m.json", "research/x/meta.json"])
            self.assertEqual(postflop.missing_files(root), ["cache/contextual/m.json", "research/x/meta.json"])
            with self.assertRaisesRegex(postflop.SolverError, "research/x/meta.json"):
                postflop.prepare(root, log=lambda m: None)  # copie sans git : on ne peut que signaler
            for f in ("cache/contextual/m.json", "research/x/meta.json"):
                (root / f).parent.mkdir(parents=True, exist_ok=True)
                (root / f).write_text("{}")
            self.assertEqual(postflop.missing_files(root), [])
            postflop.prepare(root, log=lambda m: None)


@unittest.skipUnless(POSIX, "faux solveur : script exécutable POSIX")
class SolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {"ANALYZER_HOME": self.tmp.name, "ANALYZER_SOLVER": str(FAKE_SOLVER)}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.hand = {h.hand_id: h for h in load_hands([FIXTURES])}["HAND02"]
        self.spot = postflop.build_spot(self.hand, "Hero")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_solve_interpret_and_cache(self):
        progress = []
        raw = postflop.solve(self.spot.request(), on_progress=progress.append)
        self.assertEqual(progress[0], {"tree_nodes": 12, "arena_mb": 0.1})
        self.assertEqual(progress[1]["iteration"], 10)
        result = postflop.interpret(self.spot, raw)
        self.assertEqual((result["pot_type"], result["oop"], result["iterations"]), ("pot 3bet", "H", 10))
        first, second = result["decisions"][:2]
        self.assertEqual((first["who"], first["street"], first["played"]), ("H", "f", "mise 4 bb (25 %)"))
        self.assertEqual((first["combo"], first["verdict"], first["ev_loss"]), ("QhJh", "principale", 0.0))
        self.assertEqual((second["who"], second["combo"]), ("V", "Ts9s"))  # sa main, connue à l'abattage
        self.assertEqual(first["classes"], {"AKs": [0.5, 0.05, 0.95]})
        self.assertEqual([d["i"] for d in result["decisions"]], [5, 6, 7, 8, 9, 10, 11])
        text = postflop.result_text(result, "Hero")
        self.assertIn("Flop · Toi (QhJh) — mise 4 bb (25 %)", text)
        self.assertIn("pot 3bet, toi en BB (hors position)", text)

        cached = postflop.cache_path(self.spot.request())
        self.assertTrue(cached.is_file())
        with mock.patch.dict(os.environ, {"ANALYZER_SOLVER": "/introuvable"}):
            self.assertEqual(postflop.solve(self.spot.request()), raw)  # servi par le cache
            with self.assertRaises(postflop.SolverError):
                postflop.solve(self.spot.request(), use_cache=False)

    def test_error_is_reported(self):
        with mock.patch.dict(os.environ, {"FAKE_SOLVER_FAIL": "arbre trop grand"}):
            with self.assertRaisesRegex(postflop.SolverError, "arbre trop grand"):
                postflop.solve(self.spot.request(), use_cache=False)

    def test_status(self):
        self.assertTrue(postflop.status()["ready"])
        with mock.patch.dict(os.environ, {"ANALYZER_SOLVER": str(Path(self.tmp.name) / "absent")}):
            state = postflop.status()
        self.assertFalse(state["ready"])
        self.assertIn(postflop.INSTALL_COMMAND, state["message"])

    def test_queue(self):
        queue = SolveQueue(iterations=10, target=5.0)
        self.assertEqual(queue.lookup(self.spot)["state"], "absent")
        job = queue.start(self.spot)
        self.assertIn(job["state"], ("waiting", "running", "done"))
        deadline = time.time() + 20
        while queue.get(job["job"])["state"] in ("waiting", "running") and time.time() < deadline:
            time.sleep(0.05)
        done = queue.get(job["job"])
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["result"]["decisions"][0]["who"], "H")
        self.assertEqual(queue.lookup(self.spot)["state"], "done")  # désormais en cache
        queue.shutdown()

    def test_cancel(self):
        queue = SolveQueue(iterations=10, target=5.0)
        with mock.patch.dict(os.environ, {"FAKE_SOLVER_SLEEP": "30"}):
            job = queue.start(self.spot)
            deadline = time.time() + 10
            while queue.get(job["job"])["state"] != "running" and time.time() < deadline:
                time.sleep(0.05)
            while queue._jobs[job["job"]].process is None and time.time() < deadline:
                time.sleep(0.05)
            queue.cancel(job["job"])
            while queue.get(job["job"])["state"] == "running" and time.time() < deadline:
                time.sleep(0.05)
        self.assertEqual(queue.get(job["job"])["state"], "cancelled")
        self.assertLess(time.time(), deadline)
        queue.shutdown()


@unittest.skipUnless(os.environ.get("ANALYZER_TEST_GTOPEN") and postflop.binary_path().is_file(),
                     "intégration GTOpen : ANALYZER_TEST_GTOPEN=1 et solveur installé")
class GTOpenIntegrationTest(unittest.TestCase):
    """Résolution réelle (lente) : python -m unittest tests.test_postflop avec ANALYZER_TEST_GTOPEN=1."""

    def test_real_solve(self):
        hand = {h.hand_id: h for h in load_hands([FIXTURES])}["HAND02"]
        spot = postflop.build_spot(hand, "Hero")
        raw = postflop.solve(spot.request(iterations=3, target=50.0), use_cache=False)
        result = postflop.interpret(spot, raw)
        self.assertIsNone(result["stopped"])
        self.assertEqual(len(result["decisions"]), 7)
        hero = result["decisions"][0]
        self.assertEqual((hero["who"], hero["combo"]), ("H", "QhJh"))
        self.assertAlmostEqual(sum(hero["strategy"]), 1.0, places=2)
        print(postflop.result_text(result, "Hero"), file=sys.stderr)


if __name__ == "__main__":
    unittest.main()

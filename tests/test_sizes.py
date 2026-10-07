"""Tailles théoriques dans l'arbre des coups joués : celles choisies pour le flop (ou le flop choisi le plus proche),
plus les tailles jouées, signalées dans l'explorateur."""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from analyzer.parsers import load_hands
from analyzer.theory import coach, postflop, review, studyspots

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_SOLVER = FIXTURES / "fake_solver.py"


class HomeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = {h.hand_id: h for h in load_hands([FIXTURES])}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(self.home / "home")})
        env.start()
        self.addCleanup(env.stop)


class HandTreeTest(HomeTest):
    def test_borrowed_sizes_and_played_sizes(self):
        spot = postflop.build_spot(self.hands["HAND01"], "Hero")  # SRP sur K♣7♥2♠ : c-bet 32 %, check-raise 53,7 %
        self.assertEqual((spot.family, spot.sizes_from), ("srp", {"board": "KsKd4c", "exact": False}))
        self.assertEqual(spot.plan["bet:fi:"], [33])  # 32 % joué : la théorie (33 %) suffit
        self.assertEqual(spot.plan["raise:fo::0"], [33, 53.7])  # check-raise joué, ajouté à la théorie
        self.assertEqual([(p["key"], p["how"]) for p in spot.played_sizes],
                         [("bet:fi:", "same"), ("raise:fo::0", "added")])
        request = spot.request()
        self.assertEqual(request["plan"]["raise:fo::0"], [33.0, 53.7])
        self.assertTrue(spot.menu_text().startswith("c-bet 33 % · check-raise flop 33 % / 54 %"))
        info = spot.sizes_info()
        self.assertEqual((info["source"], info["board"], info["same_texture"]), ("proche", ["Ks", "Kd", "4c"], False))
        added = info["played"][1]
        self.assertEqual((added["who"], added["label"], added["size_text"], added["theory_text"]),
                         ("V", "Check-raise flop", "54 %", "33 %"))
        legacy = postflop.build_spot(self.hands["HAND01"], "Hero", theory=False)  # l'arbre d'avant
        self.assertIsNone(legacy.plan)
        self.assertNotIn("plan", legacy.request())
        result = postflop.interpret(spot, {"decisions": [], "iterations": 1, "exploit_pct": 1.0, "seconds": 1.0,
                                           "tree_nodes": 1})
        self.assertEqual(result["sizes"]["source"], "proche")

    def test_replaced_and_exact_flop(self):
        spot = postflop.build_spot(self.hands["HAND02"], "Hero")  # pot 3bet : c-bet de la BB à 25 %
        self.assertEqual(spot.family, "3bet")
        self.assertEqual(spot.plan["bet:fo:"], [25.0])  # à la place de 33 % (proche)
        self.assertEqual(spot.played_sizes[0]["how"], "replaced")
        self.assertEqual(spot.played_sizes[-1]["key"], "bet:ri:ox")  # petite mise river du bouton
        # les tailles choisies pour ce flop (aux couleurs près) passent avant celles du flop le plus proche
        studyspots.save_selection("srp", "Kd7s2h", {"plan": {"bet:fi:": [75], "raise:fo::0": ["geo"]}})
        spot = postflop.build_spot(self.hands["HAND01"], "Hero")
        self.assertEqual(spot.sizes_from, {"board": "Kd7s2h", "exact": True})
        self.assertEqual(spot.plan["bet:fi:"], [75, 32.0])  # la c-bet jouée (32 %) s'ajoute à 75 %
        self.assertEqual(spot.plan["raise:fo::0"], ["geo", 53.7])

    def test_helpers(self):
        geo = postflop._symbolic_sizes(5.0, 97.5, 3)  # flop d'un SRP à 100 bb
        self.assertEqual((geo["geo"], geo["geo2"], geo["a"]), (121.0, 266.2, 1950.0))
        merge = postflop._merge_played
        self.assertEqual(merge(["geo"], 115.0, {"geo": 121.0}), ([115.0], "replaced"))
        self.assertEqual(merge([33], 33.4, {}), ([33], "same"))
        self.assertEqual(merge([33], 66.0, {}), ([33, 66.0], "added"))
        self.assertEqual(merge([33, "a"], "a", {}), ([33, "a"], "same"))
        self.assertEqual(merge([33], "a", {}), ([33, "a"], "added"))


def action(kind, amount=0.0, allin=False):
    return {"kind": kind, "amount": amount, "allin": allin}


class NodeTest(HomeTest):
    def test_situation_keys(self):
        check, bet, call = action("check"), action("bet", 1.6), action("call", 1.6)
        history = [
            {"kind": "action", "player": 0, "street": 0, "actions": [check, action("bet", 1.6)], "chosen": 0},
            {"kind": "action", "player": 1, "street": 0, "actions": [check, bet], "chosen": 1},
            {"kind": "action", "player": 0, "street": 0, "actions": [action("fold"), call, action("raise", 6)],
             "chosen": 1},
            {"kind": "card", "player": None, "street": 1, "actions": [], "chosen": None},
            {"kind": "action", "player": 0, "street": 1, "actions": [check, action("bet", 4)], "chosen": 0},
        ]
        node = {"type": "action", "street": 1, "player": 1, "actions": [check, action("bet", 4), action("bet", 90, True)],
                "history": history}
        self.assertEqual(postflop.situation_keys(node), [None, "bet:ti:i", "bet:ti:i"])  # 2e barrel
        flop = {"type": "action", "street": 0, "player": 0, "actions": [action("fold"), call, action("raise", 6)],
                "history": history[:2]}
        self.assertEqual(postflop.situation_keys(flop), [None, None, "raise:fo::0"])  # check-raise

    def test_mark_played_sizes(self):
        spot = postflop.build_spot(self.hands["HAND01"], "Hero")
        history = [{"kind": "action", "player": 0, "street": 0, "pot": 5.0, "put": [2.5, 2.5],
                    "actions": [action("check"), action("bet", 1.65)], "chosen": 0},
                   {"kind": "action", "player": 1, "street": 0, "pot": 5.0, "put": [2.5, 2.5],
                    "actions": [action("check"), action("bet", 1.65)], "chosen": 1}]
        # la BB face à la c-bet : check-raise 33 % (théorie) et 53,7 % (joué), en % du pot après le call (8,2)
        node = {"type": "action", "street": 0, "player": 0, "pot": 6.6, "put": [2.5, 4.1], "history": history,
                "actions": [action("fold"), action("call", 1.6), action("raise", 4.3), action("raise", 6.0),
                            action("raise", 97.5, True)]}
        postflop.mark_played_sizes(node, spot)
        flags = [a.get("played_size") for a in node["actions"]]
        self.assertEqual(flags[:3], [None, None, None])
        self.assertEqual(flags[3], {"how": "added", "size": "54 %", "theory": "33 %"})
        self.assertNotIn("played_size", history[1]["actions"][1])  # c-bet 32 % : la théorie (33 %), rien à signaler


class ReviewTest(HomeTest):
    def test_old_tree_analysis_kept(self):
        hand = self.hands["HAND01"]
        legacy = postflop.build_spot(hand, "Hero", theory=False)
        from analyzer import db
        from analyzer.db import analyses
        analyses.put(db.current(), review.digest_key(legacy), {"v": review.VERSION, "hand": hand.hand_id,
                                                                "decisions": []})
        done, todo = review.collect([hand, self.hands["HAND02"]], "Hero")
        self.assertEqual([d["hand"] for d in done], ["HAND01"])  # analysée avec les tailles fixes : gardée
        self.assertEqual([s.hand.hand_id for s in todo], ["HAND02"])
        self.assertIsNotNone(todo[0].plan)  # les nouvelles analyses prennent les tailles théoriques


@unittest.skipIf(os.name == "nt", "faux solveur : script exécutable POSIX")
class ChooseForHandTest(HomeTest):
    def test_choose_then_solve(self):
        from analyzer.app.library import Library
        folder = self.home / "mains"
        folder.mkdir()
        shutil.copy(FIXTURES / "betclic_sample.txt", folder / "sample.txt")
        lib = Library(folder)
        self.addCleanup(lib.solves.shutdown)
        with mock.patch.dict(os.environ, {"ANALYZER_SOLVER": str(self.home / "absent")}):
            self.assertEqual(lib.choose_hand_sizes("HAND01")["state"], "unavailable")
        meta = lib.explorer_state("HAND01")["meta"]
        self.assertEqual((meta["sizes"]["source"], meta["sizes"]["choose_time"]), ("proche", "1 h 10 environ"))
        with mock.patch.dict(os.environ, {"ANALYZER_SOLVER": str(FAKE_SOLVER)}):
            view = lib.choose_hand_sizes("HAND01")
            self.assertEqual(view["mode"], "choose")
            self.assertEqual(lib.solve("HAND01")["job"], view["job"])  # le coup suit la tâche du choix
            deadline = time.time() + 60
            while lib.solves.get(view["job"])["state"] in ("waiting", "running") and time.time() < deadline:
                time.sleep(0.05)
            job = lib.solves.get(view["job"])
            self.assertEqual(job["state"], "done", job.get("error"))
            spot = lib._spot("HAND01")
            self.assertTrue(spot.sizes_from["exact"])  # les tailles de ce flop, désormais choisies
            self.assertEqual(lib.solve("HAND01")["state"], "done")
            self.assertIsNotNone(lib.solves.live_for(spot))  # le coup s'explore tout de suite
            ident = "spot:srp:Kc7h2s"
            study = studyspots.parse_ident(ident)
            deadline = time.time() + 60  # puis le spot d'étude de ce flop (et son plan de jeu), qui rejoint la série
            key = postflop.study_key(study.request())
            while coach.load_plan(key) is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertIn(ident, studyspots.spot_studies())
            self.assertIn("Kc7h2s", studyspots.family_boards("srp"))


if __name__ == "__main__":
    unittest.main()

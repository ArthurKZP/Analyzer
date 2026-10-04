import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer.app.library import Library
from analyzer.app.review_page import build_review_page
from analyzer.parsers import load_hands
from analyzer.theory import review

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_SOLVER = FIXTURES / "fake_solver.py"
POSIX = os.name != "nt"


def step(player, kinds, chosen):
    return {"kind": "action", "player": player, "actions": [{"kind": k} for k in kinds], "chosen": chosen}


CARD = {"kind": "card", "player": None, "actions": [], "chosen": None}


def node(player, history):
    return {"player": player, "history": history + [step(player, [], None)]}


class SituationTest(unittest.TestCase):
    def test_keys(self):
        check, bet, call = (step(0, ["check"], 0), step(1, ["check", "bet"], 1), step(0, ["fold", "call", "raise"], 1))
        self.assertEqual(review.situation(node(1, [check])), "bet:fi:")  # c-bet du bouton
        self.assertEqual(review.situation(node(0, [check, bet])), "face:fo::1")  # BB face à la c-bet
        raise_ = step(0, ["fold", "call", "raise"], 2)
        self.assertEqual(review.situation(node(1, [check, bet, raise_])), "face:fi::2")  # face au check-raise
        turn = [check, bet, call, CARD, step(0, ["check"], 0)]
        self.assertEqual(review.situation(node(1, turn)), "bet:ti:i")  # 2e barrel
        checked = [check, step(1, ["check", "bet"], 0), CARD]
        self.assertEqual(review.situation(node(0, checked)), "bet:to:x")  # probe turn

    def test_labels(self):
        self.assertEqual(review.situation_label("bet:fi:", "srp"), "C-bet")
        self.assertEqual(review.situation_label("face:fo::1", "srp"), "Face à : C-bet")
        self.assertEqual(review.situation_label("face:fi::2", "srp"), "Face à : Check-raise flop")
        self.assertEqual(review.situation_label("bet:to:o", "3bet"), "2e barrel")
        self.assertEqual(review.situation_label("face:ti:o:1", "3bet"), "Face à : 2e barrel")


def decision(who, key, kinds, chosen, range_, ev_loss=None):
    return {"d": 0, "street": "f", "who": who, "key": key, "label": key, "chosen": chosen, "range": range_,
            "actions": [{"kind": k, "label": k} for k in kinds], "played": kinds[chosen], "approx": False,
            "combo": "AhKd" if ev_loss is not None else None, "strategy": [0.5] * len(kinds) if ev_loss is not None else None,
            "evs": None, "ev_loss": ev_loss, "frequency": None, "verdict": None, "pot": 5.0, "board": ["Ks", "7d", "2c"]}


class AggregateTest(unittest.TestCase):
    def setUp(self):
        # Face à la c-bet, il folde 15 fois sur 20 là où le solveur folderait 30 % du temps.
        folds = [decision("V", "face:fo::1", ["fold", "call", "raise"], 0, [0.3, 0.5, 0.2]) for _ in range(15)]
        calls = [decision("V", "face:fo::1", ["fold", "call", "raise"], 1, [0.3, 0.5, 0.2]) for _ in range(5)]
        mine = [decision("H", "bet:fi:", ["check", "bet"], 0, [0.4, 0.6], ev_loss=1.5),
                decision("H", "bet:fi:", ["check", "bet"], 1, [0.4, 0.6], ev_loss=0.0),
                decision("H", "face:fi::2", ["fold", "call", "raise"], 0, [0.5, 0.4, 0.1], ev_loss=0.1)]
        self.digests = [{"hand": "H1", "date": "01/01/2026 10:00", "villain": "Lui", "family": "srp",
                         "decisions": folds + calls + mine}]

    def test_costly_and_groups(self):
        costly = review.costly(self.digests, "H")
        self.assertEqual([d["ev_loss"] for _, d in costly], [1.5])  # 0,1 bb n'est pas une erreur
        groups = {g["key"]: g for g in review.by_situation(self.digests, "H")}
        self.assertEqual((groups["bet:fi:"]["n"], groups["bet:fi:"]["errors"], groups["bet:fi:"]["lost"]), (2, 1, 1.5))
        self.assertAlmostEqual(groups["bet:fi:"]["expected"]["aggressive"], 1.2)

    def test_mixed_action_costs_nothing(self):
        # Le solveur paie 35 % du temps avec cette main : l'écart d'EV (nœud peu convergé) ne compte pas.
        mixed = dict(decision("H", "face:ri:oo:2", ["fold", "call"], 1, [0.6, 0.4], ev_loss=16.6), frequency=0.35)
        rare = dict(decision("H", "bet:ri:ii", ["check", "bet"], 0, [0.1, 0.9], ev_loss=12.3), frequency=0.03)
        digests = [{"hand": "H2", "date": "", "villain": "Lui", "family": "4bet", "decisions": [mixed, rare]}]
        self.assertEqual([review.loss(d) for d in (mixed, rare)], [0.0, 12.3])
        self.assertEqual([d["ev_loss"] for _, d in review.costly(digests, "H")], [12.3])
        groups = {g["key"]: g for g in review.by_situation(digests, "H")}
        self.assertEqual((groups["face:ri:oo:2"]["errors"], groups["face:ri:oo:2"]["lost"]), (0, 0.0))

    def test_villain_deviation_and_exploit(self):
        group = review.by_situation(self.digests, "V")[0]
        devs = {d["category"]: d for d in review.deviations(group)}
        self.assertEqual((devs["fold"]["observed"], round(devs["fold"]["expected"], 2)), (0.75, 0.3))
        self.assertEqual(devs["fold"]["confidence"], "solide")
        self.assertIn("folde trop", review.exploit(group, devs["fold"], villain=True))
        self.assertIn("aggressive", devs)  # il ne relance jamais (théorie : 20 %)
        self.assertEqual(review.deviations(dict(group, n=5)), [])  # trop peu d'occurrences


@unittest.skipUnless(POSIX, "faux solveur : script exécutable POSIX")
class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        env = {"ANALYZER_HOME": str(self.folder / "home"), "ANALYZER_SOLVER": str(FAKE_SOLVER)}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.hands = load_hands([FIXTURES / "betclic_sample.txt"])

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_analyze_collect_and_pages(self):
        done, todo = review.collect(self.hands, "Hero")
        self.assertEqual((len(done), len(todo)), (0, 2))  # SRP et pot 3bet ; tapis préflop et limp exclus
        logs = []
        self.assertEqual(review.analyze(self.hands, "Hero", log=logs.append), 2)
        done, todo = review.collect(self.hands, "Hero")
        self.assertEqual((len(done), len(todo)), (2, 0))
        digest = next(g for g in done if g["hand"] == "HAND02")
        self.assertEqual((digest["family"], digest["villain"]), ("3bet", "Villain"))
        keys = [(d["who"], d["key"]) for d in digest["decisions"]]
        self.assertIn(("H", "bet:fo:"), keys)  # la BB 3betteuse c-bette (ou checke) au flop
        self.assertTrue(all(len(d["actions"]) > 1 for d in digest["decisions"]))
        self.assertTrue(any(d["who"] == "V" and d["combo"] for d in digest["decisions"]))  # cartes montrées
        files = list(review.review_dir().glob("*.json"))
        self.assertEqual(len(files), 2)
        self.assertLess(max(f.stat().st_size for f in files), 60_000)
        page = build_review_page(self.hands, "Hero")
        for text in ("2 / 2</b> mains analysées", "Les erreurs qui coûtent le plus", "Les erreurs récurrentes"):
            self.assertIn(text, page)
        villain_page = build_review_page(self.hands, "Hero", "Villain")
        self.assertIn("Ses écarts à exploiter", villain_page)
        self.assertNotIn("Ses écarts à exploiter", page)

    def test_library_queue(self):
        lib = Library(FIXTURES)
        try:
            state = lib.review_state()
            self.assertEqual((state["total"], state["done"], state["busy"]), (2, 0, 0))
            lib.review_state(start=True)
            import time
            deadline = time.time() + 30
            while lib.review_state()["busy"] and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(lib.review_state("Villain")["done"], 2)
            self.assertIn("Face au solveur", lib.player_page("Villain", "solveur"))
            self.assertIn("2 / 2</b>", lib.self_page("solveur"))
            # le résumé survit au cache des résultats (clé du spot seul)
            for path in (Path(os.environ["ANALYZER_HOME"]) / "resolutions").glob("*.json"):
                path.unlink()
            self.assertEqual(review.collect(self.hands, "Hero")[0][0]["hand"] in ("HAND01", "HAND02"), True)
            self.assertEqual(len(review.collect(self.hands, "Hero")[1]), 0)
            json.dumps(lib.review_cancel())
        finally:
            lib.solves.shutdown()


if __name__ == "__main__":
    unittest.main()

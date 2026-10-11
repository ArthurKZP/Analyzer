import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from analyzer import players
from analyzer.app.library import Library
from analyzer.app.server import start
from analyzer.stats import PlayerStats, Ratio

FIXTURES = Path(__file__).parent / "fixtures"


def stats(name="X", hands=200, **pcts):
    """Stats factices : pcts = {"sb_first.call": 40, …} sur 50 occasions chacune."""
    st = PlayerStats(name)
    st.hands = hands
    for key, pct in pcts.items():
        st.ratios[key.replace("__", ".")] = Ratio(hits=round(pct / 2), opps=50)
    return st


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_suggestion(self):
        reg = stats(sb_first__call=2, sb_first__raise=84, bb_vs_open__fold=18, bb_vs_open__raise=26)
        self.assertEqual(players.suggest(reg), ("reg", []))
        rec = stats(sb_first__call=40, sb_first__raise=44, bb_vs_open__raise=2)
        kind, reasons = players.suggest(rec)
        self.assertEqual(kind, "rec")
        self.assertIn("limpe 40 % de ses boutons", reasons)
        self.assertEqual(len(reasons), 3)
        self.assertEqual(players.suggest(stats(sb_first__call=40))[0], None)  # un seul signal : à confirmer
        self.assertEqual(players.suggest(stats(hands=20, sb_first__call=40, sb_first__raise=10)), (None, []))
        few = stats(sb_first__call=40, sb_first__raise=10)
        few.ratios["sb_first.call"] = Ratio(hits=4, opps=10)  # trop peu d'occasions pour compter
        self.assertEqual(players.suggest(few)[0], None)
        self.assertEqual(players.suggest(None), (None, []))

    def test_classify_and_manual_choice(self):
        data = {"A": stats("A", sb_first__call=40, sb_first__raise=44), "B": stats("B", sb_first__raise=85)}
        out = players.classify(["A", "B", "C"], data)
        self.assertEqual([(out[n]["kind"], out[n]["source"]) for n in "ABC"],
                         [("rec", "suggestion"), ("reg", "suggestion"), ("reg", "défaut")])
        players.set_kind("B", "rec")
        players.set_kind("A", "reg")
        out = players.classify(["A", "B"], data)
        self.assertEqual((out["A"]["kind"], out["A"]["source"], out["A"]["suggestion"]), ("reg", "toi", "rec"))
        self.assertEqual(players.describe(out["B"]), "Récréatif (classé par toi)")
        players.set_kind("B", None)  # retour à la suggestion
        self.assertEqual(players.load(), {"A": "reg"})  # gardé dans la base
        with self.assertRaises(ValueError):
            players.set_kind("A", "fish")


class ByKindTest(unittest.TestCase):
    def test_results_by_kind(self):
        from analyzer.selfreport import by_kind_html
        rows = [{"name": "A", "hands": 100, "net_bb": 20.0}, {"name": "B", "hands": 50, "net_bb": -5.0}]
        html = by_kind_html(rows, {"A": {"kind": "reg"}, "B": {"kind": "rec"}})
        self.assertIn("Contre les réguliers</div><div class=\"value\">+20,0 bb/100", html)
        self.assertIn("Contre les récréatifs</div><div class=\"value\">−10,0 bb/100", html)
        self.assertEqual(by_kind_html(rows, {}), "")  # un seul type : pas de séparation


class LibraryKindsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        shutil.copy(FIXTURES / "betclic_sample.txt", Path(cls.tmp.name) / "sample.txt")
        cls.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(Path(cls.tmp.name) / "home"),
                                               "ANALYZER_SOLVER": str(Path(cls.tmp.name) / "absent")})
        cls.env.start()
        cls.lib = Library(cls.tmp.name)
        cls.server = start(cls.lib, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.lib.solves.shutdown()
        cls.env.stop()
        cls.tmp.cleanup()

    def post(self, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=30)
        conn.request("POST", "/api/joueurs", json.dumps(payload), {"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, json.loads(body)

    def test_recreational_hands_leave_the_theory(self):
        lib = self.lib
        villain = lib.summary()["opponents"][0]
        self.assertEqual((villain["name"], villain["kind"], villain["source"]), ("Villain", "reg", "défaut"))
        self.assertIn("contre les réguliers", lib.self_page("solveur"))
        self.assertIn("Tes décisions", lib.self_page("solveur"))
        self.assertNotIn("contre des récréatifs", lib.self_page("preflop"))

        status, summary = self.post({"name": "Villain", "kind": "rec"})
        self.assertEqual((status, summary["opponents"][0]["kind"]), (200, "rec"))
        self.assertEqual(lib.regular_hands()[1], {"hands": len(lib.hands), "players": ["Villain"]})
        self.assertIn("contre des récréatifs", lib.self_page("solveur"))
        self.assertIn("0 / 0</b> mains analysées", lib.self_page("solveur"))
        self.assertIn("contre des récréatifs", lib.self_page("preflop"))  # Mon préflop : sans ses mains
        self.assertEqual(lib.review_state()["total"], 0)
        page = lib.player_page("Villain", "solveur")
        self.assertIn("Ses écarts à exploiter", page)
        self.assertNotIn("Les erreurs qui coûtent le plus", page)
        preflop = lib.player_page("Villain", "preflop")
        self.assertIn("classé récréatif", preflop)
        self.assertNotIn("Décisions comparées", preflop)
        self.assertIn("<td>Récréatif</td>", lib.self_page("bilan"))  # colonne Type des résultats

        self.assertEqual(self.post({"name": "Villain", "kind": None})[0], 200)
        self.assertNotIn("contre des récréatifs", lib.self_page("preflop"))
        self.assertEqual(self.post({"name": "Personne", "kind": "rec"})[0], 404)
        for bad in ({"name": "Villain", "kind": "fish"}, {"name": 3, "kind": "rec"}, ["Villain"]):
            self.assertEqual(self.post(bad)[0], 400, bad)


if __name__ == "__main__":
    unittest.main()

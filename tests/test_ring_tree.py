"""Explorateur, coup à plusieurs : l'arbre préflop 6-max tiré des charts (ring_tree), jusqu'au flop d'une paire de
positions quelconque, puis le spot d'étude de cette paire (famille 6-max hors des séries, tailles empruntées)."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from analyzer.theory import ring_ranges, ring_tree, studyspots

# Charts synthétiques (pas ceux d'un site) : l'UTG et le CO ouvrent, le BTN et la BB défendent face au CO.
LINES = {
    "UTG:raise BB:call": {"UTG": "AA,KK,QQ,AKs", "BB": "JJ,TT"},
    "CO:raise BB:call": {"CO": "AA,KK,QQ,JJ,AKs,AKo,KQs", "BB": "TT,99,AQs"},
    "CO:raise BB:raise CO:call": {"CO": "QQ,JJ,AKo", "BB": "AA,KK,AKs"},
    "CO:raise BB:raise CO:raise BB:call": {"CO": "AA,KK", "BB": "AA"},
    "CO:raise BTN:call": {"CO": "AA,KK,QQ,JJ,AKs,AKo,KQs", "BTN": "TT,99,AQs:0.5"},
}


def charts():
    ring_ranges.save_solution("6-max", {"format": "6-max", "source": "Charts de test — synthétiques",
                                        "lines": {k: {"ranges": v} for k, v in LINES.items()}})


class Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)


class FamiliesTest(unittest.TestCase):
    def test_other_positions(self):
        info = studyspots.ring_family("6max_bb_utg_srp")
        self.assertEqual((info["oop"], info["ip"], info["pot"], info["stack"], info["structure"]),
                         ("BB", "UTG", 5.5, 97.5, "srp_ip"))
        self.assertEqual(info["label"], "6-max, BB contre UTG, SRP : open de l'UTG à 2,5 bb, call de la BB, 100 bb")
        three = studyspots.ring_family("6max_utg_hj_3bet")  # le HJ 3bette l'UTG, en position
        self.assertEqual((three["pot"], three["structure"]), (16.5, "3bet_ip"))  # les deux blindes mortes
        self.assertIsNone(studyspots.ring_family("6max_btn_bb_srp"))  # le bouton n'est jamais hors de position
        self.assertIsNone(studyspots.ring_family("6max_bb_lj_srp"))
        self.assertTrue(studyspots.is_ident("spot:6max_co_btn_srp:KsKd4c"))
        self.assertFalse(studyspots.is_ident("spot:6max_btn_co_srp:KsKd4c"))
        self.assertTrue(studyspots.is_ring("6max_co_btn_srp") and not studyspots.is_ring("srp"))
        self.assertEqual(studyspots.family_title("6max_co_btn_srp"), "6-max, CO contre BTN, SRP")
        self.assertEqual(studyspots.flop_set("6max_co_btn_srp"), [])  # hors des séries
        self.assertNotIn("6max_co_btn_srp", studyspots.RING_FAMILIES)
        self.assertEqual(studyspots.size_models("6max_co_btn_srp"), ["6max_sb_bb_srp"])  # l'ouvreur hors de position
        self.assertEqual(studyspots.size_models("6max_bb_utg_srp"), ["6max_bb_btn_srp", "srp"])


class SpotTest(Home):
    def test_spot_borrows_sizes(self):
        charts()
        studyspots.save_selection("6max_sb_bb_srp", "KsKd4c", {"plan": {"bet:fo:": [75]}})
        spot = studyspots.parse_ident("spot:6max_co_btn_srp:KsKd4c")
        self.assertEqual((spot.oop, spot.ip, spot.pot_bb, spot.context), ("CO", "BTN", 6.5, "6-max|CO:raise BTN:call"))
        self.assertEqual(spot.ranges["BTN"]["AQs"], 0.5)
        self.assertEqual((spot.plan, spot.sizes_from, spot.sizes_family), ({"bet:fo:": [75]}, "KsKd4c", "6max_sb_bb_srp"))
        self.assertIn("(tailles de K♠K♦4♣, 6-max, SB contre BB, SRP)", spot.menu_text())
        options = studyspots.flop_options("6max_co_btn_srp", ["4c", "Kd", "Ks"])
        self.assertEqual((options["id"], options["series"]), ("spot:6max_co_btn_srp:KsKd4c", False))
        self.assertEqual(options["cost"], "au plus une dizaine de minutes sur 4 cœurs")  # pas de choix des tailles


class TreeTest(Home):
    def test_without_charts(self):
        with self.assertRaises(ring_tree.NoCharts):
            ring_tree.node([])

    def test_first_in_and_defence(self):
        charts()
        first = ring_tree.node([])
        self.assertEqual((first["type"], first["positions"], first["player"]), ("action", ["UTG", ""], 0))
        self.assertEqual([a["name"] for a in first["actions"]], ["Fold", "Open 2,5"])
        aces = next(r for r in first["hands"][0] if r[0] == "AsAh")
        self.assertEqual(aces[4:], [0.0, 1.0])  # fold, open
        self.assertEqual(first["hands"][1], [])  # personne d'autre en jeu
        hj = ring_tree.node(["fold"])
        self.assertEqual(hj["preflop"]["keys"], ["fold"])  # pas d'open du HJ dans ces charts
        btn = ring_tree.node(["fold", "fold", "raise"])
        self.assertEqual((btn["positions"], btn["player"], btn["preflop"]["label"]),
                         (["CO", "BTN"], 1, "BTN face à l'open du CO"))
        self.assertEqual(btn["preflop"]["keys"], ["fold", "call"])  # pas de 3bet du BTN dans ces charts
        self.assertEqual((btn["pot"], btn["put"]), (4.0, [2.5, 0.0]))
        opens = {r[0] for r in btn["hands"][0]}
        self.assertEqual(len(opens), 6 * 4 + 4 + 12 + 4)  # AA KK QQ JJ, AKs, AKo, KQs
        flop = ring_tree.node(["fold", "fold", "raise", "call"])
        self.assertEqual((flop["type"], flop["positions"], flop["pot"]), ("flop", ["CO", "BTN"], 6.5))
        info = flop["preflop"]
        self.assertEqual((info["family"], info["series"], info["family_name"]), ("6max_co_btn_srp", False,
                                                                                 "SRP · CO contre BTN"))
        self.assertEqual(len(info["spots"]), len(studyspots.flop_set("srp")))  # les flops types
        self.assertEqual(ring_tree.line_for_family("6max_co_btn_srp"), ["fold", "fold", "raise", "call"])

    def test_threebet_and_fourbet(self):
        charts()
        line = ["fold", "fold", "raise", "fold", "fold", "raise"]  # le CO ouvre, la BB 3bette
        facing = ring_tree.node(line)
        self.assertEqual((facing["preflop"]["label"], facing["preflop"]["keys"], facing["positions"]),
                         ("CO face au 3bet de la BB", ["fold", "call", "raise"], ["BB", "CO"]))
        self.assertEqual((facing["pot"], facing["put"]), (13.0, [10.0, 2.5]))  # 3bet hors de position : 10 bb
        strategy = {r[0]: r[4:] for r in facing["hands"][1]}
        self.assertEqual((strategy["QsQh"], strategy["AsAh"], strategy["KsQs"]),
                         ([0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]))
        four = ring_tree.node(line + ["raise"])
        self.assertEqual((four["preflop"]["label"], four["preflop"]["keys"], four["put"]),
                         ("BB face au 4bet du CO", ["fold", "call"], [10.0, 22.0]))
        calls = {r[0]: r[4:] for r in four["hands"][0]}
        self.assertEqual((calls["AsAh"], calls["KsKh"]), ([0.0, 1.0], [1.0, 0.0]))
        flop = ring_tree.node(line + ["raise", "call"])
        self.assertEqual((flop["preflop"]["family"], flop["pot"]), ("6max_bb_co_4bet", 44.5))
        self.assertTrue(flop["preflop"]["series"])  # une série d'étude
        self.assertEqual(ring_tree.line_for_family("6max_bb_co_4bet"), line + ["raise", "call"])

    def test_ends(self):
        charts()
        walk = ring_tree.node(["fold"] * 5)
        self.assertEqual((walk["type"], walk["positions"]), ("terminal_fold", ["BB", ""]))
        alone = ring_tree.node(["raise"] + ["fold"] * 5)
        self.assertEqual((alone["type"], alone["pot"]), ("terminal_fold", 4.0))
        for bad in (["call"], ["fold"] * 6, ["raise"] + ["fold"] * 6):
            with self.assertRaises(ValueError):
                ring_tree.node(bad)


class EmptyTablesPageTest(unittest.TestCase):
    def test_load_charts_without_hands(self):
        """Sans main à plusieurs, les charts se chargent quand même : ils servent à l'explorateur."""
        from analyzer.app.ring_page import build_ring_page
        page = build_ring_page([], "Hero", ranges={})
        self.assertIn("Aucune main à une table de 3 joueurs ou plus", page)
        self.assertIn('class="rg-load"', page)
        self.assertNotIn('class="rg-load"', build_ring_page([], "Hero", ranges={"6-max": 3}))


class ServerTest(Home):
    def setUp(self):
        super().setUp()
        from analyzer.app.library import Library
        from analyzer.app.server import start
        folder = Path(os.environ["ANALYZER_HOME"]).parent / "mains"
        folder.mkdir(parents=True)
        self.lib = Library(folder)
        self.server = start(self.lib, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.lib.solves.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=30)
        conn.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, json.loads(data)

    def test_routes(self):
        status, body = self.post("/api/explorateur/preflop", {"table": "6-max", "line": []})
        self.assertEqual(status, 404)
        self.assertIn("Pas encore de charts 6-max", body["error"])
        charts()
        status, body = self.post("/api/explorateur/preflop", {"table": "6-max", "line": ["fold", "fold", "raise"]})
        self.assertEqual((status, body["positions"]), (200, ["CO", "BTN"]))
        status, body = self.post("/api/explorateur/preflop", {"table": "6-max", "family": "6max_co_btn_srp"})
        self.assertEqual((status, body["type"], [h.get("position") for h in body["history"][:-1]]),
                         (200, "flop", ["UTG", "HJ", "CO", "BTN"]))
        self.assertEqual(self.post("/api/explorateur/preflop", {"table": "6-max", "line": ["call"]})[0], 404)
        for bad in ({"table": "6-max", "line": "fold"}, {"table": "6-max", "line": ["tapis"]},
                    {"table": "6-max", "family": "srp"}, {"table": "6-max", "family": "6max_btn_bb_srp"}):
            self.assertEqual(self.post("/api/explorateur/preflop", bad)[0], 400, bad)
        status, body = self.post("/api/explorateur/flop", {"family": "6max_co_btn_srp", "board": ["Ks", "Kd", "4c"]})
        self.assertEqual((status, body["id"]), (200, "spot:6max_co_btn_srp:KsKd4c"))
        status, body = self.post("/api/explorateur/flop", {"family": "6max_bb_utg_srp", "board": ["Ks", "Kd", "4c"]})
        self.assertEqual(status, 200)  # l'UTG contre la BB : la ligne est dans les charts
        status, body = self.post("/api/explorateur/flop", {"family": "6max_sb_btn_3bet", "board": ["Ks", "Kd", "4c"]})
        self.assertEqual(status, 400)  # pas de cette ligne dans les charts
        self.assertIn("Pas de ranges 6-max", body["error"])


if __name__ == "__main__":
    unittest.main()

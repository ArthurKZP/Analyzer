import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from analyzer.app.library import Library
from analyzer.app.server import start
from analyzer.theory import custom_tree, postflop

try:
    from .base import IsolatedHome
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"
CHECK = {"kind": "check", "amount": 0.0}
BET = {"kind": "bet", "amount": 13.0}


def flop_node(path=None):
    """Le BTN au flop K♠K♦4♣ après le check de la BB : check ou mise de 13 dans un pot de 52."""
    return {"type": "action", "player": 1, "street": 0, "board": ["Ks", "Kd", "4c"], "pot": 52.0, "put": [0.0, 0.0],
            "actions": [CHECK, BET], "path": path or [{"type": "action", "index": 0}],
            "history": [{"kind": "action", "player": 0, "street": 0, "pot": 52.0, "put": [0.0, 0.0],
                         "actions": [CHECK, BET], "chosen": 0}]}


STRATEGY = [["AdAc", 0.1, 0.9], ["AsAc", 0.2, 0.8], ["AhAc", 0.3, 0.7], ["QhQd", 0.5, 0.5], ["QsQd", 0.4, 0.6]]


class SizesTest(IsolatedHome):
    def test_keys_and_sizes(self):
        for key in ("bet:fi:", "bet:ro:xo", "raise:fo::0", "raise:ti:io:2"):
            self.assertEqual(custom_tree.check_key(key), key)
        for bad in ("bet:fx:", "raise:fo:", "bet:fi:iii", "call:fi:", None):
            with self.assertRaises(ValueError):
                custom_tree.check_key(bad)
        self.assertEqual(custom_tree.check_sizes("bet:fi:", [75, "a", 33, "geo", 33.0, 12.5]), [12.5, 33, 75, "geo", "a"])
        self.assertEqual(custom_tree.check_sizes("raise:fo::0", ["x3", 50, "x2,5"]), [50, "x2.5", "x3"])
        self.assertEqual(custom_tree.check_sizes("bet:fi:", []), [])  # plus de mise : check seulement
        for key, sizes in (("bet:fi:", ["x3"]), ("bet:fi:", [0]), ("bet:fi:", [1001]), ("bet:fi:", [True]),
                           ("raise:fo::0", ["x1"]), ("raise:fo::0", ["xa"]), ("bet:fi:", list(range(1, 10))),
                           ("bet:fi:", "33")):
            with self.assertRaises(ValueError):
                custom_tree.check_sizes(key, sizes)

    def test_set_and_reset(self):
        self.assertFalse(custom_tree.edited(custom_tree.load("H1")))
        self.assertEqual(custom_tree.set_sizes("H1", "bet:fi:", [50, 33], [33]), 0)
        self.assertEqual(custom_tree.load("H1")["plan"], {"bet:fi:": [33, 50]})
        custom_tree.put_lock("H1", {"path": [], "combos": {}, "edits": []})
        self.assertEqual(custom_tree.set_sizes("H1", "raise:fo::0", ["x3"], [50]), 1)  # les verrous sont retirés
        self.assertEqual(custom_tree.load("H1"), {"plan": {"bet:fi:": [33, 50], "raise:fo::0": ["x3"]}, "locks": []})
        custom_tree.set_sizes("H1", "bet:fi:", [33], [33])  # comme l'arbre d'origine : plus de modification
        self.assertEqual(custom_tree.load("H1")["plan"], {"raise:fo::0": ["x3"]})
        custom_tree.reset_sizes("H1")
        self.assertFalse(custom_tree.edited(custom_tree.load("H1")))
        self.assertIsNone(custom_tree.merged_plan(None, {"plan": {}}))
        self.assertEqual(custom_tree.merged_plan({"bet:fi:": [33], "bet:ti:i": [50]}, {"plan": {"bet:fi:": [75]}}),
                         {"bet:fi:": [75], "bet:ti:i": [50]})


class LocksTest(IsolatedHome):
    def test_suit_mirrors(self):
        self.assertEqual(len(custom_tree.board_perms(["Ks", "Kd", "4c"])), 2)  # pique et carreau s'échangent
        self.assertEqual(len(custom_tree.board_perms(["Ah", "Kd", "7c"])), 1)
        self.assertEqual(len(custom_tree.board_perms(["Ah", "Kh", "7h"])), 6)
        perms = custom_tree.board_perms(["Ks", "Kd", "4c"])
        self.assertEqual(custom_tree.mirrors("AdAc", perms), {frozenset({"Ad", "Ac"}), frozenset({"As", "Ac"})})
        self.assertEqual(len(custom_tree.mirrors("AcAd", custom_tree.board_perms(["Ah", "Kh", "7h"]))), 3)
        self.assertEqual(custom_tree.normalize([1, 3], 2), [0.25, 0.75])
        for bad in ([1], [0, 0], [-1, 2], ["a", 1], None):
            with self.assertRaises(ValueError):
                custom_tree.normalize(bad, 2)

    def test_lock_from(self):
        node = dict(flop_node(), labels=["Check", "Mise 13 (25 %)"], title="Flop · BTN, après BB check")
        lock = custom_tree.lock_from(node, STRATEGY, [{"label": "AA", "combos": ["AdAc"], "freqs": [1, 0]}])
        self.assertEqual(lock["combos"]["AdAc"], [1.0, 0.0])
        self.assertEqual(lock["combos"]["AsAc"], [1.0, 0.0])  # son équivalent de couleur sur ce board
        self.assertEqual(lock["combos"]["AhAc"], [0.3, 0.7])  # les autres mains gardent leur stratégie
        self.assertEqual((lock["edited"], lock["actions"], lock["player"]), (["AdAc", "AsAc"], node["labels"], 1))
        self.assertEqual(lock["edits"], [{"label": "AA", "freqs": [1.0, 0.0], "combos": 2}])
        again = custom_tree.lock_from(node, STRATEGY, [{"label": "QQ", "combos": ["QhQd", "QsQd"], "freqs": [1, 1]}],
                                      lock)
        self.assertEqual([e["label"] for e in again["edits"]], ["AA", "QQ"])  # le changement d'avant reste listé
        self.assertEqual(again["combos"]["QsQd"], [0.5, 0.5])
        with self.assertRaises(ValueError):  # main absente de la range
            custom_tree.lock_from(node, STRATEGY, [{"label": "72o", "combos": ["7h2c"], "freqs": [1, 0]}])
        with self.assertRaises(ValueError):
            custom_tree.lock_from(node, STRATEGY, [])
        with self.assertRaises(ValueError):  # une seule action : rien à choisir
            custom_tree.lock_from(dict(node, actions=[CHECK]), STRATEGY, [{"combos": ["AdAc"], "freqs": [1]}])

    def test_store_and_request(self):
        path = flop_node()["path"]
        lock = custom_tree.lock_from(flop_node(), STRATEGY, [{"label": "AA", "combos": ["AdAc"], "freqs": [1, 0]}])
        custom_tree.put_lock("H1", lock)
        custom_tree.put_lock("H1", dict(lock, title="remplacé"))  # même nœud : remplacé
        data = custom_tree.load("H1")
        self.assertEqual([x["title"] for x in data["locks"]], ["remplacé"])
        self.assertEqual(custom_tree.lock_at(data, path)["title"], "remplacé")
        self.assertIsNone(custom_tree.lock_at(data, []))
        locks = custom_tree.request_locks(data)
        self.assertEqual(locks[0]["path"], path)
        self.assertEqual(locks[0]["mode"]["kind"], "hands")
        self.assertEqual(len(locks[0]["mode"]["edits"]), len(STRATEGY))  # toutes les mains du joueur
        self.assertIn({"combo": "AsAc", "freqs": [1.0, 0.0]}, locks[0]["mode"]["edits"])
        for k in range(custom_tree.MAX_LOCKS - 1):
            custom_tree.put_lock("H1", dict(lock, path=[{"type": "action", "index": k + 1}]))
        with self.assertRaises(ValueError):
            custom_tree.put_lock("H1", dict(lock, path=[{"type": "action", "index": 99}]))
        custom_tree.remove_lock("H1", 0)
        self.assertEqual(len(custom_tree.load("H1")["locks"]), custom_tree.MAX_LOCKS - 1)
        with self.assertRaises(ValueError):
            custom_tree.remove_lock("H1", 50)
        custom_tree.remove_lock("H1")
        self.assertFalse(custom_tree.edited(custom_tree.load("H1")))


class FakeSession:
    """Une session de résolution ouverte, sans le solveur : le nœud du BTN au flop et la stratégie de ses mains."""

    def __init__(self):
        self.asked = []

    def ask(self, query):
        self.asked.append(query)
        return {"node": flop_node(query["path"]), "strategy": STRATEGY}

    def node(self, path):
        node = flop_node(path)
        node["hands"] = [[], []]
        return node


class LibraryTreeTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        shutil.copy(FIXTURE, Path(tmp.name) / "sample.txt")
        self.lib = Library(tmp.name)
        self.addCleanup(self.lib.solves.shutdown)

    def test_sizes(self):
        lib = self.lib
        reference = postflop.study_key(lib._spot("HAND01").request())
        state = lib.tree_state("HAND01")
        self.assertFalse(state["edited"])
        keys = [x["key"] for x in state["situations"]]
        self.assertEqual(keys[:3], ["bet:fi:", "raise:fo::0", "raise:fi::1"])  # dans l'ordre du coup
        state = lib.set_tree_sizes("HAND01", {"bet:fi:": [75, 33]})
        cbet = next(x for x in state["situations"] if x["key"] == "bet:fi:")
        self.assertEqual((state["edited"], cbet["base"], cbet["sizes"], cbet["changed"]), (True, [33], [33, 75], True))
        request = lib._spot("HAND01").request()
        self.assertEqual(request["plan"]["bet:fi:"], [33.0, 75.0])
        self.assertNotEqual(postflop.study_key(request), reference)  # une étude à part
        state = lib.set_tree_sizes("HAND01", {"bet:to:i": [50]})  # un donk, absent du plan : ajouté
        self.assertIn("bet:to:i", [x["key"] for x in state["situations"]])
        for plan in ({"bet:fi:": [0]}, {"call:fi:": [33]}, {}, {"bet:fi:": "33"}):
            with self.assertRaises(ValueError):
                lib.set_tree_sizes("HAND01", plan)
        self.assertFalse(lib.reset_tree("HAND01")["edited"])
        self.assertEqual(postflop.study_key(lib._spot("HAND01").request()), reference)
        self.assertEqual(lib.solve("HAND01")["edits"], None)

    def test_locks(self):
        lib = self.lib
        path = flop_node()["path"]
        with self.assertRaises(ValueError):  # il faut la résolution ouverte
            lib.add_lock("HAND01", path, [{"label": "AA", "combos": ["AdAc"], "freqs": [1, 0]}])
        session = FakeSession()
        with mock.patch.object(lib.solves, "live_for", return_value=session):
            state = lib.add_lock("HAND01", path, [{"label": "AA", "combos": ["AdAc"], "freqs": [1, 0]}],
                                 ["Check", "Mise 13 (25 %)"])
            self.assertEqual(session.asked[0], {"path": path, "strategy": True})
            self.assertEqual(state["locks"][0]["title"], "Flop · BTN, après BB check")
            self.assertEqual(state["locks"][0]["actions"], ["Check", "Mise 13 (25 %)"])
            node = lib.explorer_node("HAND01", path)["node"]
            self.assertEqual(node["lock"]["edited"], ["AdAc", "AsAc"])
            self.assertEqual(lib.solve("HAND01")["edits"], {"sizes": 0, "locks": 1})
            lib.add_lock("HAND01", path, [{"label": "QQ", "combos": ["QhQd"], "freqs": [0, 1]}], ["?"])  # libellés
            lock = custom_tree.load("HAND01")["locks"][0]
            self.assertEqual([e["label"] for e in lock["edits"]], ["AA", "QQ"])
            self.assertEqual(lock["actions"], ["Check", "Mise 25 % (13 bb)"])  # ceux du serveur, faute de mieux
        request = lib._spot("HAND01").request()
        self.assertEqual(request["locks"][0]["path"], path)
        state = lib.set_tree_sizes("HAND01", {"bet:fi:": [50]})
        self.assertEqual((state["removed_locks"], state["locks"]), (1, []))  # les chemins ne mènent plus au même nœud
        lib.reset_tree("HAND01")
        with mock.patch.object(lib.solves, "live_for", return_value=session):
            lib.add_lock("HAND01", path, [{"label": "AA", "combos": ["AdAc"], "freqs": [1, 0]}])
        self.assertEqual(lib.remove_lock("HAND01", 0)["locks"], [])
        self.assertFalse(lib.tree_state("HAND01")["edited"])

    def test_study_spot(self):
        state = self.lib.set_tree_sizes("spot:srp:KsKd4c", {"bet:fi:": [25, 75]})
        self.assertTrue(state["edited"])
        spot = self.lib._spot("spot:srp:KsKd4c")
        self.assertEqual(spot.request()["plan"]["bet:fi:"], [25.0, 75.0])
        self.assertFalse(self.lib.tree_state("spot:3bet:KsKd4c")["edited"])  # chaque spot a son arbre


class TreeRoutesTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        shutil.copy(FIXTURE, Path(tmp.name) / "sample.txt")
        library = Library(tmp.name)
        self.addCleanup(library.solves.shutdown)
        self.server = start(library, port=0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def post(self, path, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=30)
        conn.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, json.loads(data or b"null")

    def test_routes(self):
        status, state = self.post("/api/explorateur/arbre", {"hand": "HAND01", "path": []})
        self.assertEqual((status, state["edited"], state["live"]), (200, False, False))
        status, state = self.post("/api/explorateur/arbre/tailles", {"hand": "HAND01", "plan": {"bet:fi:": [50]}})
        self.assertEqual((status, state["edited"], state["removed_locks"]), (200, True, 0))
        self.assertEqual(self.post("/api/explorateur/arbre/tailles", {"hand": "HAND01", "plan": {"bet:fi:": [0]}})[0],
                         400)
        status, error = self.post("/api/explorateur/arbre/verrou", {"hand": "HAND01", "path": [],
                                                                     "edits": [{"combos": ["AdAc"], "freqs": [1, 0]}]})
        self.assertEqual(status, 400)
        self.assertIn("session active", error["error"])
        self.assertEqual(self.post("/api/explorateur/arbre/deverrouiller", {"hand": "HAND01", "index": "a"})[0], 400)
        self.assertEqual(self.post("/api/explorateur/arbre/deverrouiller", {"hand": "HAND01", "index": None})[0], 200)
        self.assertEqual(self.post("/api/explorateur/arbre/origine", {"hand": "HAND01"})[1]["edited"], False)
        self.assertEqual(self.post("/api/explorateur/arbre", {"hand": "INCONNUE"})[0], 404)
        self.assertEqual(self.post("/api/explorateur/arbre/autre", {"hand": "HAND01"})[0], 400)


if __name__ == "__main__":
    unittest.main()

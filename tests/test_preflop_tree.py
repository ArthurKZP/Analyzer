import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import http.client
from pathlib import Path
from unittest import mock

from analyzer.app.library import Library
from analyzer.app.server import STATIC, start
from analyzer.theory import preflop_tree

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"


class PreflopTreeTest(unittest.TestCase):
    def test_combos(self):
        self.assertEqual(len(preflop_tree.combos("QQ")), 6)
        self.assertEqual(preflop_tree.combos("AKs"), ["AsKs", "AhKh", "AdKd", "AcKc"])
        self.assertEqual(len(preflop_tree.combos("72o")), 12)
        self.assertEqual(len(preflop_tree.classes()), 169)
        self.assertEqual(sum(len(preflop_tree.combos(c)) for c in preflop_tree.classes()), 1326)

    def test_lines(self):
        root = preflop_tree.node([])
        self.assertEqual((root["type"], root["player"], root["pot"]), ("action", 1, 1.5))  # le bouton ouvre
        self.assertEqual([a["name"] for a in root["actions"]], ["Fold", "Open 2,5"])
        self.assertEqual(len(root["hands"][1][0]), 4 + 2)  # combo, présence, éq., EV, puis fold / open
        vs_open = preflop_tree.node(["raise"])
        self.assertEqual(vs_open["player"], 0)
        self.assertEqual([a["name"] for a in vs_open["actions"]], ["Fold", "Call", "3bet 11,5", "Tapis 100"])
        self.assertEqual(vs_open["history"][0]["chosen"], 1)  # l'open, dans le déroulé
        btn = sum(r[1] for r in vs_open["hands"][1])
        self.assertAlmostEqual(btn / 1326, 0.822, delta=0.01)  # la range d'open : 82 % des mains
        self.assertEqual(len(vs_open["hands"][0]), 1326)  # la BB a toutes ses mains face à l'open
        srp = preflop_tree.node(["raise", "call"])
        self.assertEqual((srp["type"], srp["pot"], srp["stacks"], srp["preflop"]["family"]), ("flop", 5.0, [97.5, 97.5], "srp"))
        self.assertEqual(len(srp["preflop"]["spots"]), 24)
        self.assertTrue(all(s["id"].startswith("spot:srp:") for s in srp["preflop"]["spots"]))
        four = preflop_tree.node(preflop_tree.FAMILY_LINES["4bet"])
        self.assertEqual((four["pot"], four["preflop"]["family"]), (52, "4bet"))
        self.assertEqual(preflop_tree.node(["fold"])["type"], "terminal_fold")
        self.assertEqual(preflop_tree.node(["raise", "raise", "allin"])["type"], "allin")
        for bad in (["call"], ["fold", "call"], ["raise"] * 4, ["x"]):
            with self.assertRaises(ValueError):
                preflop_tree.node(bad)


class PreflopServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        shutil.copy(FIXTURE, Path(cls.tmp.name) / "sample.txt")
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

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=30)
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_explorer_from_preflop(self):
        status, body = self.request("GET", "/explorateur/preflop")
        self.assertEqual(status, 200)
        self.assertIn(b'data-hand="preflop"', body)
        status, body = self.request("POST", "/api/explorateur/preflop", json.dumps({"line": ["raise"]}))
        self.assertEqual((status, json.loads(body)["player"]), (200, 0))
        status, body = self.request("POST", "/api/explorateur/preflop", json.dumps({"family": "3bet"}))
        self.assertEqual(json.loads(body)["preflop"]["line"], ["raise", "raise", "call"])
        self.assertEqual(self.request("POST", "/api/explorateur/preflop", json.dumps({"line": ["call"]}))[0], 404)
        for bad in ({"line": "raise"}, {"line": ["tapis"]}, {"line": ["raise"] * 7}, []):
            self.assertEqual(self.request("POST", "/api/explorateur/preflop", json.dumps(bad))[0], 400, bad)
        if shutil.which("node"):
            check = subprocess.run(["node", "--check", str(STATIC / "explorer.js")], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_flop_options_route(self):
        status, body = self.request("POST", "/api/explorateur/flop", json.dumps({"family": "srp", "board": ["4c", "Kd", "Ks"]}))
        data = json.loads(body)
        self.assertEqual((status, data["id"], data["pattern"]), (200, "spot:srp:KsKd4c", "rainbow"))
        for bad in ({"family": "5bet", "board": ["Ks", "Kd", "4c"]}, {"family": "srp", "board": ["Ks", "Ks", "4c"]},
                    {"family": "srp", "board": ["Ks", "Kd"]}, {"family": "srp", "board": "KsKd4c"}):
            self.assertEqual(self.request("POST", "/api/explorateur/flop", json.dumps(bad))[0], 400, bad)

    def test_studies_sections(self):
        status, body = self.request("GET", "/etudes/srp")
        self.assertEqual(status, 200)
        self.assertIn("Spots d'étude · SRP".encode(), body)
        self.assertNotIn("Coups joués".encode(), body)
        self.assertIn("Coups joués".encode(), self.request("GET", "/etudes/coups")[1])
        self.assertIn("Spots d'étude · pot 4bet".encode(), self.request("GET", "/etudes/4bet")[1])
        self.assertEqual(self.request("GET", "/etudes/autre")[0], 404)


if __name__ == "__main__":
    unittest.main()

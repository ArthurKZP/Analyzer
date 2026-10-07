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

from analyzer.app import trainer
from analyzer.app.library import Library
from analyzer.app.server import STATIC, start

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"


def entry(key="bet:fi:", loss=0.0, family="srp", **extra):
    return dict({"family": family, "spot": "spot:srp:KsKd4c", "key": key, "combo": "AhQd", "played": "Check",
                 "best": "Mise 1,7 (33 %)", "loss": loss, "freq": 0.4}, **extra)


class TrainerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_situation_labels(self):
        labels = trainer.situation_labels("srp")
        self.assertEqual(labels["bet:fi:"], "C-bet")
        self.assertEqual(labels["face:fo::1"], "Face à : C-bet")  # la BB face à la c-bet
        self.assertEqual(labels["face:fi::2"], "Face à : Check-raise flop")
        self.assertEqual(labels["face:ri:ix:1"], "Face à : Probe river (c-bet payée, turn checkée)")
        self.assertNotIn("bet:fo:", labels)  # pas de donk en SRP
        self.assertEqual(trainer.situation_labels("3bet")["bet:fo:"], "C-bet")

    def test_hands_info(self):
        info = trainer.hands_info(["Ks", "7d", "2c", "9h", "3s"], [["Ah", "Kd"], ["Qc", "Qd"]])
        self.assertEqual(info["winner"], 0)
        self.assertEqual(info["holdings"][0], "Top paire")
        flop = trainer.hands_info(["Ks", "7d", "2c"], [["Ah", "Kd"], ["Qd", "Jd"]])
        self.assertNotIn("winner", flop)  # pas d'abattage avant la river
        split = trainer.hands_info(["As", "Ks", "Qs", "Js", "Ts"], [["2c", "3d"], ["4c", "5d"]])
        self.assertIsNone(split["winner"])
        self.assertTrue(trainer.valid_cards(["Ah", "Kd"], 2))
        for bad in (["Ah", "Ah"], ["Zz", "Kd"], "AhKd", ["Ah"], [1, 2]):
            self.assertFalse(trainer.valid_cards(bad, 2), bad)

    def test_journal_and_progress(self):
        self.assertEqual(trainer.progress()["all"]["n"], 0)
        bad = [entry(family="5bet"), entry(loss=-1), entry(loss="1"), entry(key="x" * 100), "texte",
               {k: v for k, v in entry().items() if k != "combo"}]
        self.assertEqual(trainer.record(bad)["saved"], 0)
        reply = trainer.record([entry(loss=1.5), entry(loss=0.1), entry("face:fo::1", loss=0.0, played="Fold")])
        self.assertEqual(reply["saved"], 3)
        progress = reply["progress"]
        self.assertEqual((progress["all"]["n"], progress["all"]["errors"], progress["recent"]["n"]), (3, 1, 3))
        first = progress["situations"][0]  # celle qui coûte le plus en tête
        self.assertEqual((first["key"], first["label"], first["n"], first["errors"]), ("bet:fi:", "C-bet", 2, 1))
        self.assertAlmostEqual(first["good"], 0.5)
        lines = trainer.journal_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(set(json.loads(lines[0])), {"family", "spot", "key", "combo", "played", "best", "loss",
                                                     "freq", "t"})
        with trainer.journal_path().open("a", encoding="utf-8") as f:
            f.write("pas du json\n" + json.dumps({"family": "srp"}) + "\n")
        self.assertEqual(trainer.progress()["all"]["n"], 3)  # lignes abîmées ignorées
        self.assertEqual(trainer.clear()["all"]["n"], 0)
        self.assertFalse(trainer.journal_path().exists())

    def test_overview_without_studies(self):
        data = trainer.overview()
        self.assertEqual(set(data["families"]), {"srp", "3bet", "4bet"})
        self.assertEqual(data["families"]["srp"]["spots"], [])
        self.assertIn("face:fo::1", data["families"]["4bet"]["situations"])


class TrainerServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        shutil.copy(FIXTURE, Path(cls.tmp.name) / "sample.txt")
        cls.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(Path(cls.tmp.name) / "home"),
                                               "ANALYZER_SOLVER": str(Path(cls.tmp.name) / "absent")})
        cls.env.start()
        cls.server = start(Library(cls.tmp.name), port=0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.env.stop()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def post(self, path, payload):
        status, data = self.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        return status, json.loads(data) if data else None

    def test_page_and_static(self):
        status, body = self.request("GET", "/entraineur")
        self.assertEqual(status, 200)
        self.assertIn("Entraîneur".encode(), body)
        for name in ("trainer.js", "trainer.css"):
            self.assertEqual(self.request("GET", "/static/" + name)[0], 200)
        if shutil.which("node"):
            check = subprocess.run(["node", "--check", str(STATIC / "trainer.js")], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_api(self):
        status, body = self.request("GET", "/api/entraineur")
        data = json.loads(body)
        self.assertEqual((status, data["ready"], data["families"]["srp"]["spots"]), (200, False, []))
        status, info = self.post("/api/entraineur/mains", {"board": ["Ks", "7d", "2c", "9h", "3s"],
                                                           "holes": [["Ah", "Kd"], ["Qc", "Qd"]]})
        self.assertEqual((status, info["winner"]), (200, 0))
        for bad in ({"board": ["Ks", "7d"], "holes": [["Ah", "Kd"], ["Qc", "Qd"]]},
                    {"board": ["Ks", "7d", "2c"], "holes": [["Ks", "Kd"], ["Qc", "Qd"]]},  # carte en double
                    {"board": ["Ks", "7d", "2c"], "holes": [["Ah", "Kd"]]}, {"board": "Ks7d2c"}, []):
            self.assertEqual(self.post("/api/entraineur/mains", bad)[0], 400, bad)
        status, reply = self.post("/api/entraineur/resultat", {"entries": [entry(loss=0.8)]})
        self.assertEqual((status, reply["saved"], reply["progress"]["all"]["errors"]), (200, 1, 1))
        self.assertEqual(self.post("/api/entraineur/resultat", [entry()])[0], 400)
        status, progress = self.post("/api/entraineur/effacer", {})
        self.assertEqual((status, progress["all"]["n"]), (200, 0))
        foreign = {"Origin": "http://evil.example", "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/entraineur/effacer", "{}", foreign)[0], 403)


if __name__ == "__main__":
    unittest.main()

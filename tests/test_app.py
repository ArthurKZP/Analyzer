import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from analyzer.app.library import Library, UnknownPlayer
from analyzer.app.server import start

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"
FAKE_SOLVER = Path(__file__).parent / "fixtures" / "fake_solver.py"


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_folder(self):
        lib = Library(self.folder / "hands")
        self.assertEqual(lib.summary()["hands"], 0)
        self.assertIsNone(lib.hero)
        with self.assertRaises(UnknownPlayer):
            lib.self_page("bilan")

    def test_pages_are_built_once(self):
        shutil.copy(FIXTURE, self.folder / "sample.txt")
        lib = Library(self.folder)
        summary = lib.summary()
        self.assertEqual((summary["hero"], summary["hands"]), ("Hero", 4))
        self.assertEqual([o["name"] for o in summary["opponents"]], ["Villain"])
        plan = lib.player_page("Villain", "plan")
        self.assertIn("Plan de jeu", plan)
        self.assertNotIn("<h1>", plan)  # intégré : le titre est dans l'application
        self.assertIs(plan, lib.player_page("Villain", "plan"))
        self.assertIn("Ses lignes", lib.player_page("Villain", "rapport"))
        self.assertIn('id="data"', lib.player_page("Villain", "spots"))
        self.assertIn('"solver":true', lib.player_page("Villain", "spots"))  # bouton « Résoudre ce coup »
        preflop = lib.player_page("Villain", "preflop")
        self.assertIn("Lui face au solveur", preflop)
        self.assertIn('href="spots#hand=HAND03"', preflop)
        self.assertIn("Bouton face au 3bet", lib.self_page("preflop"))
        self.assertIn("Résultats par adversaire", lib.self_page("bilan"))
        self.assertIn('id="data"', lib.self_page("spots"))
        with self.assertRaises(UnknownPlayer):
            lib.player_page("Personne", "plan")
        with self.assertRaises(KeyError):
            lib.player_page("Villain", "inconnue")

    def test_solve_states(self):
        shutil.copy(FIXTURE, self.folder / "sample.txt")
        lib = Library(self.folder)
        env = {"ANALYZER_HOME": str(self.folder / "home"), "ANALYZER_SOLVER": str(self.folder / "absent")}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(lib.solve("HAND03")["state"], "unsupported")
            absent = lib.solve("HAND02")
            self.assertEqual(absent["state"], "absent")
            self.assertFalse(absent["solver"]["ready"])
            self.assertEqual(lib.solve("HAND02", start=True)["state"], "unavailable")
            with self.assertRaises(UnknownPlayer):
                lib.solve("PERSONNE")
            if os.name == "nt":
                return
            os.environ["ANALYZER_SOLVER"] = str(FAKE_SOLVER)
            job = lib.solve("HAND02", start=True)
            deadline = time.time() + 20
            while lib.solves.get(job["job"])["state"] in ("waiting", "running") and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(lib.solve("HAND02")["state"], "done")
            state = lib.explorer_state("HAND02")
            self.assertTrue(state["live"])
            self.assertEqual((state["meta"]["hero_cards"], state["meta"]["hero_position"]), (["Qh", "Jh"], "BB"))
            reply = lib.explorer_node("HAND02", [{"type": "action", "index": 1}])
            self.assertEqual((reply["live"], reply["node"]["type"]), (True, "action"))
            # catégories des mains pour les filtres, alignées sur les mains de chaque joueur
            self.assertEqual([len(c) for c in reply["node"]["cats"]], [len(h) for h in reply["node"]["hands"]])
            self.assertEqual(state["categories"]["made"][0], ["sf", "Quinte flush"])
        lib.solves.shutdown()

    def test_import(self):
        lib = Library(self.folder)
        content = FIXTURE.read_text(encoding="utf-8")
        result = lib.import_files([
            {"name": "../../piege.txt", "content": content},
            {"name": "copie.txt", "content": content},
            {"name": "autre.txt", "content": "PokerStars Hand #1"},
            {"name": "vide.txt", "content": "  "},
        ])
        self.assertEqual(result["added"], 4)
        self.assertEqual([f["status"] for f in result["files"]],
                         ["importé", "déjà importé", "format non reconnu", "vide"])
        saved = list(self.folder.iterdir())
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].name.startswith("import-") and saved[0].name.endswith("-piege.txt"))
        self.assertEqual(result["state"]["hands"], 4)
        self.assertEqual(lib.import_files([{"name": "x.txt", "content": content}])["added"], 0)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        shutil.copy(FIXTURE, Path(cls.tmp.name) / "sample.txt")
        cls.env = mock.patch.dict(os.environ, {"ANALYZER_HOME": str(Path(cls.tmp.name) / "home"),
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
        return resp.status, resp.getheader("Content-Type", ""), data

    def test_pages(self):
        status, ctype, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Analyzer", body)
        self.assertEqual(self.request("GET", "/static/app.js")[0], 200)
        status, _, body = self.request("GET", "/api/state")
        self.assertEqual(json.loads(body)["hero"], "Hero")
        self.assertEqual(self.request("GET", "/p/Villain/plan")[0], 200)
        self.assertEqual(self.request("GET", "/moi/bilan")[0], 200)
        self.assertEqual(self.request("GET", "/p/Villain/preflop")[0], 200)
        self.assertEqual(self.request("GET", "/moi/preflop")[0], 200)

    def test_solver_endpoints(self):
        status, ctype, body = self.request("GET", "/api/solveur")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["ready"])
        headers = {"Content-Type": "application/json"}
        status, _, body = self.request("POST", "/api/resoudre", json.dumps({"hand": "HAND03"}), headers)
        self.assertEqual((status, json.loads(body)["state"]), (200, "unsupported"))
        status, _, body = self.request("POST", "/api/resoudre", json.dumps({"hand": "HAND02", "start": True}), headers)
        self.assertEqual(json.loads(body)["state"], "unavailable")
        self.assertEqual(self.request("POST", "/api/resoudre", json.dumps({"hand": "NOPE"}), headers)[0], 404)
        self.assertEqual(self.request("POST", "/api/resoudre", "pas du json", headers)[0], 400)
        self.assertEqual(self.request("GET", "/api/resoudre/inconnu")[0], 404)
        self.assertEqual(self.request("POST", "/api/resoudre/inconnu/arreter", "{}", headers)[0], 404)
        foreign = {"Origin": "http://evil.example", **headers}
        self.assertEqual(self.request("POST", "/api/resoudre", json.dumps({"hand": "HAND02"}), foreign)[0], 403)

    def test_explorer(self):
        status, ctype, body = self.request("GET", "/explorateur/HAND02")
        self.assertEqual(status, 200)
        self.assertIn(b'data-hand="HAND02"', body)
        self.assertEqual(self.request("GET", "/explorateur/PERSONNE")[0], 404)
        self.assertEqual(self.request("GET", "/static/explorer.js")[0], 200)
        headers = {"Content-Type": "application/json"}
        status, _, body = self.request("POST", "/api/explorateur/etat", json.dumps({"hand": "HAND02"}), headers)
        state = json.loads(body)
        self.assertEqual((status, state["state"], state["meta"]["villain"]), (200, "absent", "Villain"))
        state = json.loads(self.request("POST", "/api/explorateur/etat", json.dumps({"hand": "HAND03"}), headers)[2])
        self.assertEqual(state["state"], "unsupported")
        bad_paths = [[{"type": "action", "index": "0"}], [{"type": "card", "card": "Zz"}], [{"type": "x"}],
                     [{"type": "action", "index": 0, "extra": 1}], "chemin", [{"type": "action", "index": 0}] * 41]
        for path in bad_paths:
            body = json.dumps({"hand": "HAND02", "path": path})
            self.assertEqual(self.request("POST", "/api/explorateur/noeud", body, headers)[0], 400, path)
        body = json.dumps({"hand": "HAND02", "path": [{"type": "action", "index": 1}, {"type": "card", "card": "Ah"}]})
        status, _, reply = self.request("POST", "/api/explorateur/noeud", body, headers)
        self.assertEqual((status, json.loads(reply)["state"]), (409, "session"))  # ni session ni cache
        body = json.dumps({"hand": "HAND03", "path": []})
        self.assertEqual(self.request("POST", "/api/explorateur/noeud", body, headers)[0], 404)

    def test_studies_page(self):
        status, _, body = self.request("GET", "/etudes")
        self.assertEqual(status, 200)
        self.assertIn("Aucune étude".encode(), body)
        headers = {"Content-Type": "application/json"}
        status, _, body = self.request("POST", "/api/etudes/supprimer", json.dumps({"key": "../x"}), headers)
        self.assertEqual((status, json.loads(body)), (200, {"ok": False}))

    def test_not_found(self):
        for path in ("/p/Personne/plan", "/p/Villain/autre", "/static/server.py",
                     "/static/..%2Fserver.py", "/rien"):
            self.assertEqual(self.request("GET", path)[0], 404, path)

    def test_foreign_host_and_origin_are_refused(self):
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        body = json.dumps({"files": []})
        status = self.request("POST", "/api/import", body, {"Origin": "http://evil.example",
                                                             "Content-Type": "application/json"})[0]
        self.assertEqual(status, 403)

    def test_import_endpoint(self):
        body = json.dumps({"files": [{"name": "doublon.txt", "content": FIXTURE.read_text(encoding="utf-8")}]})
        status, _, data = self.request("POST", "/api/import", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["files"][0]["status"], "déjà importé")
        self.assertEqual(self.request("POST", "/api/import", "pas du json")[0], 400)


if __name__ == "__main__":
    unittest.main()

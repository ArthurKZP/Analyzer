import http.client
import json
import os
import shutil
import subprocess
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
        self.assertIn("Les bluffs de Villain", lib.player_page("Villain", "bluffs"))
        population = lib.self_page("bluffs")
        self.assertIn("Les bluffs des réguliers", population)
        self.assertIn("Les réguliers ensemble : Villain.", population)
        with self.assertRaises(UnknownPlayer):
            lib.player_page("Personne", "plan")
        with self.assertRaises(KeyError):
            lib.player_page("Villain", "inconnue")

    @unittest.skipIf(os.name == "nt", "faux solveur : script exécutable POSIX")
    def test_prepare_plan(self):
        # « Préparer le plan » : ouvre l'étude enregistrée et en tire le plan de jeu
        from analyzer.theory import coach, postflop, studyspots
        shutil.copy(FIXTURE, self.folder / "sample.txt")
        lib = Library(self.folder)
        env = {"ANALYZER_HOME": str(self.folder / "home"), "ANALYZER_SOLVER": str(FAKE_SOLVER)}
        spot = studyspots.StudySpot("srp", ["Ks", "7d", "2c"], plan={})
        request = spot.request()
        with mock.patch.dict(os.environ, env), mock.patch.object(coach, "missing", return_value=[spot.ident]), \
                mock.patch.object(studyspots, "parse_ident", return_value=spot):
            study = postflop.study_path(request)
            study.parent.mkdir(parents=True)
            study.write_text(json.dumps(request), encoding="utf-8")  # le faux solveur recharge la requête
            state = lib.plan_state(start=True)
            self.assertEqual((state["missing"], state["busy"]), (1, 1))
            deadline = time.time() + 30
            while lib.solves.plan_view(spot)["state"] in ("waiting", "running") and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(lib.solves.plan_view(spot)["state"], "done", lib.solves.plan_view(spot).get("error"))
            plan = coach.load_plan(postflop.study_key(request))
            self.assertEqual(plan["id"], spot.ident)
            self.assertIn("cbet", plan["nodes"])
        lib.solves.shutdown()

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

    def test_client_gone(self):
        # le navigateur abandonne des requêtes (page rechargée) : pas d'erreur, le serveur continue
        import io
        import socket
        import struct
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            for path in ("/moi/bluffs", "/p/Villain/rapport", "/etudes/plan"):
                s = socket.create_connection(("127.0.0.1", self.port))
                s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # coupure brutale
                s.close()
            time.sleep(1.5)
            self.assertEqual(self.request("GET", "/moi/bluffs")[0], 200)
        self.assertNotIn("Traceback", err.getvalue())

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
        self.assertIn("Aucun coup résolu".encode(), body)
        self.assertIn("Spots d'étude · SRP".encode(), body)
        self.assertIn("0 / 24</b> flops résolus".encode(), body)
        if shutil.which("node"):  # le script de la page doit au moins être du JavaScript valide
            script = body.decode().rsplit("<script>", 1)[1].split("</script>")[0]
            path = Path(self.tmp.name) / "etudes.js"
            path.write_text(script, encoding="utf-8")
            check = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
        headers = {"Content-Type": "application/json"}
        status, _, body = self.request("POST", "/api/etudes/supprimer", json.dumps({"key": "../x"}), headers)
        self.assertEqual((status, json.loads(body)), (200, {"ok": False}))

    def test_study_spots(self):
        status, _, body = self.request("GET", "/explorateur/spot:srp:KsKd4c")
        self.assertEqual(status, 200)
        self.assertIn(b'data-hand="spot:srp:KsKd4c"', body)
        for bad in ("/explorateur/spot:srp:KsKs4c", "/explorateur/spot:autre:KsKd4c", "/api/spots/autre"):
            self.assertEqual(self.request("GET", bad)[0], 404, bad)
        series = json.loads(self.request("GET", "/api/spots/srp")[2])
        self.assertEqual((series["total"], series["done"], series["ready"]), (24, 0, False))
        self.assertEqual(series["rows"][0]["id"], "spot:srp:KsKd4c")
        headers = {"Content-Type": "application/json"}
        status, _, body = self.request("POST", "/api/spots/srp/resoudre", "{}", headers)
        self.assertEqual((status, json.loads(body)["busy"]), (200, 0))  # solveur absent : rien n'est lancé
        self.assertEqual(self.request("POST", "/api/spots/autre/resoudre", "{}", headers)[0], 404)
        self.assertEqual(self.request("POST", "/api/spots/srp/arreter", "{}", headers)[0], 200)
        state = json.loads(self.request("POST", "/api/explorateur/etat", json.dumps({"hand": "spot:srp:Ah7d2c"}),
                                        headers)[2])
        self.assertEqual((state["state"], state["meta"]["spot"], state["meta"]["texture"]), ("absent", True, "Ace high"))
        status, _, body = self.request("POST", "/api/explorateur/noeud",
                                       json.dumps({"hand": "spot:srp:Ah7d2c", "path": []}), headers)
        self.assertEqual((status, json.loads(body)["state"]), (409, "session"))
        body = json.dumps({"hand": "spot:srp:AhAh2c"})
        self.assertEqual(self.request("POST", "/api/explorateur/etat", body, headers)[0], 404)

    def test_backup_endpoints(self):
        status, _, body = self.request("GET", "/api/sauvegarde")
        view = json.loads(body)
        self.assertEqual((status, view["dest"], view["running"]), (200, "", None))
        headers = {"Content-Type": "application/json"}
        for bad in ({"dest": 3, "studies": False, "auto": False}, {"dest": "x"}, {"dest": "x" * 600, "studies": False,
                                                                                    "auto": False}):
            self.assertEqual(self.request("POST", "/api/sauvegarde/reglages", json.dumps(bad), headers)[0], 400)
        dest = str(Path(self.tmp.name) / "sauvegardes-test")
        body = json.dumps({"dest": dest, "studies": True, "auto": False})
        view = json.loads(self.request("POST", "/api/sauvegarde/reglages", body, headers)[2])
        self.assertEqual((view["dest"], view["studies"]), (dest, True))
        self.request("POST", "/api/sauvegarde/lancer", "{}", headers)
        deadline = time.time() + 20
        while json.loads(self.request("GET", "/api/sauvegarde")[2])["running"] and time.time() < deadline:
            time.sleep(0.05)
        view = json.loads(self.request("GET", "/api/sauvegarde")[2])
        self.assertEqual((view["error"], view["last"]["dest"]), (None, dest))
        foreign = {"Origin": "http://evil.example", **headers}
        self.assertEqual(self.request("POST", "/api/sauvegarde/lancer", "{}", foreign)[0], 403)

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

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

from analyzer.app.import_job import FINISH_COST, READ_COST, ImportJob
from analyzer.app.library import Library, UnknownPlayer
from analyzer.app.server import start

try:
    from .base import IsolatedHome, renumbered, seen_by
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome, renumbered, seen_by

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"
FAKE_SOLVER = Path(__file__).parent / "fixtures" / "fake_solver.py"


class LibraryTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

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
        self.assertIn('"K9s"', lib.self_page("mains"))  # mains de départ : les données de la page
        self.assertIn('id="data"', lib.self_page("spots"))
        self.assertIn("Les bluffs de Villain", lib.player_page("Villain", "bluffs"))
        population = lib.self_page("bluffs")
        self.assertIn("Les bluffs des réguliers", population)
        self.assertIn("Un seul régulier.", population)  # plus de liste de noms en tête de page
        self.assertIs(lib.field_page("bluffs"), population)  # Étude du field : le même onglet
        field = lib.field_page("joueurs")
        self.assertIn("<h2>En heads-up</h2>", field)
        self.assertIn('data-name="Villain"', field)
        self.assertNotIn("Aux tables à plusieurs</h2>", field)  # pas de mains à plusieurs
        with self.assertRaises(KeyError):
            lib.field_page("inconnue")
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
        env = {"ANALYZER_DB": "", "ANALYZER_HOME": str(self.folder / "home"), "ANALYZER_SOLVER": str(FAKE_SOLVER)}
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

    def test_students(self):
        with mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(self.folder / "home")}):
            main = Library(self.folder / "moi")  # aucune main à moi
            paul = main.create_student("Paul", None)
            self.assertEqual((paul["id"], paul["hands"]), ("paul", 0))
            student = main.student("paul")
            self.assertIs(student.solves, main.solves)  # une seule file de résolution pour tout le monde
            added = student.import_files([{"name": "s.txt", "content": FIXTURE.read_text(encoding="utf-8")}])["added"]
            self.assertEqual(added, 4)
            self.assertEqual([(s["name"], s["hands"], s["hero"]) for s in main.students_summary()], [("Paul", 4, "Hero")])
            self.assertEqual(main.find_hand("HAND02")[1], "Hero")  # ses mains s'ouvrent dans l'explorateur
            page = student.self_page("leaks")
            self.assertIn("Leakfinding de Paul", page)
            self.assertIn("<h2>Ses adversaires</h2>", page)  # le résultat de l'élève, pas celui de l'adversaire
            self.assertIn('data-sort="net">Résultat de Paul</th>', page)
            self.assertIn('data-api="/api/eleves/paul/revue"', student.self_page("solveur"))
            text, error = main.coach._run_tool("leakfinding", {"eleve": "Paul"})
            self.assertFalse(error, text)
            self.assertEqual(json.loads(text)["eleve"], "Paul")
            main.set_kind("Villain", "rec")  # l'adversaire d'un élève se classe comme les tiens
            self.assertEqual(student.leaks_report().scope_hands["rec"], 4)
            with self.assertRaises(UnknownPlayer):
                main.student("inconnu")
            main.solves.shutdown()

    def test_solve_states(self):
        shutil.copy(FIXTURE, self.folder / "sample.txt")
        lib = Library(self.folder)
        env = {"ANALYZER_DB": "", "ANALYZER_HOME": str(self.folder / "home"), "ANALYZER_SOLVER": str(self.folder / "absent")}
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
            # déroulé : l'action préflop jouée et le tapis effectif
            self.assertEqual([s["name"] for s in state["meta"]["preflop"]], ["Open 2", "3bet 8", "Call"])
            self.assertEqual(state["meta"]["stack"], 104.0)
            reply = lib.explorer_node("HAND02", [{"type": "action", "index": 1}])
            self.assertEqual((reply["live"], reply["node"]["type"]), (True, "action"))
            self.assertIn("settled", reply["node"])  # mains quasi absentes : meilleure action selon l'EV
            self.assertEqual(len(reply["node"]["presence"]), 2)
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
            {"name": "autre.txt", "content": "Partie n° 1"},
            {"name": "tournoi.txt", "content": "PokerStars Hand #1: Tournament #2, $1+$0.10 USD Hold'em No Limit - "
                                               "Level I (10/20) - 2023/11/01 10:00:00 ET"},
            {"name": "vide.txt", "content": "  "},
        ])
        self.assertEqual(result["added"], 4)
        self.assertEqual([f["status"] for f in result["files"]],
                         ["importé", "déjà importé", "format non reconnu", "aucune main lue", "vide"])
        self.assertEqual(list(self.folder.iterdir()), [])  # rien dans le dossier : tout est dans la base
        files = lib.db.all("SELECT nom, mains FROM fichiers WHERE espace_id = ?", (lib.space_id,))
        self.assertEqual(files, [("piege.txt", 4)])  # le nom d'origine ne sert jamais de chemin
        self.assertEqual(result["state"]["hands"], 4)
        self.assertEqual(lib.import_files([{"name": "x.txt", "content": content}])["added"], 0)

    def test_remove_pseudos(self):
        """Les mains importées d'un autre compte (son pseudo marqué comme héros) : supprimées de la base, écartées des
        imports suivants, puis rétablies ; celles où il joue contre toi restent."""
        text = FIXTURE.read_text(encoding="utf-8")
        lib = Library(self.folder)
        lib.import_files([{"name": "moi.txt", "content": text},
                          {"name": "lui.txt", "content": seen_by(renumbered(text, "1"), "Villain")}])
        view = lib.pseudos_view()
        self.assertEqual([(p["name"], p["hands"], p["sites"], p["role"]) for p in view["pseudos"]],
                         [("Hero", 4, ["Betclic"], "me"), ("Villain", 4, ["Betclic"], "me")])
        for bad in ("Villain", [], ["Personne"], [3]):
            with self.assertRaises(ValueError):
                lib.remove_pseudos(bad)
        out = lib.remove_pseudos(["Villain"])
        self.assertEqual((out["removed"], out["kept"], lib.hero, out["state"]["hands"]), (4, 0, "Hero", 4))
        self.assertEqual([p["name"] for p in out["pseudos"]["pseudos"]], ["Hero"])
        self.assertEqual([(r["name"], r["hands"]) for r in out["pseudos"]["removed"]], [("Villain", 4)])
        self.assertEqual([o["name"] for o in lib.summary()["opponents"]], ["Villain"])  # toujours ton adversaire
        result = lib.import_files([{"name": "lui2.txt", "content": seen_by(renumbered(text, "2"), "Villain")}])
        self.assertEqual((result["added"], result["skipped"], result["files"][0]["status"]),
                         (0, 4, "écarté (pseudo supprimé)"))
        with self.assertRaises(KeyError):
            lib.restore_pseudo("Hero")
        back = lib.restore_pseudo("Villain")
        self.assertEqual((back["added"], back["pseudos"]["removed"], len(lib.pseudos_view()["pseudos"])), (8, [], 2))
        lib.set_settings({"hero_excluded": ["Villain"]})  # décoché : pas toi
        self.assertEqual({p["name"]: p["role"] for p in lib.pseudos_view()["pseudos"]}, {"Hero": "me", "Villain": "other"})

    def test_import_progress(self):
        job = ImportJob("essai", known=100)  # 100 mains déjà là ; lire une main coûte READ_COST fois la relire
        job.plan([10, 30])
        job.reading(5)
        self.assertEqual(job.view()["progress"], round(5 * READ_COST / (40 * READ_COST + 140 * (1 + FINISH_COST)), 3))
        job.file_done(10)
        job.reading(30)
        job.file_done(30)
        self.assertEqual(job.view()["hands"], [40, 40])
        job.loading(70, 120)  # des doublons : 120 mains à relire, pas 140
        view = job.view()
        self.assertEqual((view["step"], view["progress"]),
                         ("analyses", round((40 * READ_COST + 70) / (40 * READ_COST + 120 * (1 + FINISH_COST)), 3)))
        job.finish({"added": 20})
        self.assertEqual((job.view()["state"], job.view()["progress"]), ("done", 1.0))

    def test_import_in_background(self):
        lib = Library(self.folder)
        self.addCleanup(lib.solves.shutdown)
        content = FIXTURE.read_text(encoding="utf-8")
        job = lib.start_import([{"name": "a.txt", "content": content}, {"name": "vide.txt", "content": ""}])
        self.assertEqual(job["step"], "lecture")
        deadline = time.time() + 30
        while lib.import_status(job["id"])["state"] == "running" and time.time() < deadline:
            time.sleep(0.02)
        done = lib.import_status(job["id"])
        self.assertEqual((done["state"], done["progress"], done["step"]), ("done", 1.0, "analyses"))
        self.assertEqual((done["files"], done["hands"], done["loaded"]), ([2, 2], [4, 4], [4, 4]))
        self.assertEqual([f["status"] for f in done["result"]["files"]], ["importé", "vide"])
        self.assertEqual(len(lib.hands), 4)
        self.assertIsNone(lib.import_status("inconnu"))


def make_zip(entries: dict) -> bytes:
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class ZipImportTest(IsolatedHome):
    """Une archive zip d'historiques, avec ses dossiers : chacun s'importe comme un fichier."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "mains"
        sites = Path(__file__).parent / "sites"
        self.betclic = FIXTURE.read_text(encoding="utf-8")
        self.winamax = (sites / "winamax.txt").read_text(encoding="utf-8")

    def test_archive_with_folders(self):
        import base64
        inner = make_zip({"Unibet/table.txt": (Path(__file__).parent / "sites" / "unibet.txt").read_bytes()})
        data = make_zip({
            "Historiques/Betclic/2026-01/hu.txt": "\ufeff" + self.betclic,  # avec BOM
            "Historiques/Winamax/table.txt": self.winamax.encode("utf-8"),
            "Historiques/Winamax/copie.txt": self.winamax.encode("utf-8"),
            "Historiques/notes.pdf": b"%PDF",
            "__MACOSX/Historiques/._hu.txt": b"x",
            "Historiques/.DS_Store": b"x",
            "Historiques/autres.zip": inner,
            "Historiques/vide.txt": b"",
        })
        lib = Library(self.folder)
        try:
            result = lib.import_files([{"name": "mains.zip", "zip": base64.b64encode(data).decode()},
                                       {"name": "abime.zip", "zip": base64.b64encode(b"pas un zip").decode()},
                                       {"name": "envoi.zip", "zip": "@@@"}])
            rows = {r["name"]: r for r in result["files"]}
            self.assertEqual(rows["mains.zip › Historiques/Betclic/2026-01/hu.txt"]["status"], "importé")
            self.assertEqual(rows["mains.zip › Historiques/Winamax/copie.txt"]["status"], "déjà importé")
            self.assertEqual(rows["mains.zip › Historiques/autres.zip/Unibet/table.txt"]["sites"], ["Unibet"])
            self.assertEqual(rows["mains.zip › Historiques/vide.txt"]["status"], "vide")
            archive = rows["mains.zip"]
            self.assertTrue(archive["archive"])
            self.assertEqual(archive["status"], "archive : 5 historique(s), 1 autre(s) fichier(s) laissé(s) de côté")
            self.assertEqual(archive["new"], result["added"])
            self.assertEqual(rows["abime.zip"]["status"], "archive zip illisible")
            self.assertEqual(rows["envoi.zip"]["status"], "archive zip illisible (envoi abîmé)")
            saved = lib.db.all("SELECT nom FROM fichiers WHERE espace_id = ? ORDER BY nom", (lib.space_id,))
            self.assertEqual([n for (n,) in saved], [  # un par historique nouveau, avec sa place dans l'archive
                "mains.zip › Historiques/Betclic/2026-01/hu.txt", "mains.zip › Historiques/Winamax/table.txt",
                "mains.zip › Historiques/autres.zip/Unibet/table.txt"])
            self.assertEqual(result["state"]["hands"], len(lib.hands))
            self.assertEqual(lib.import_files([{"name": "mains.zip", "zip": base64.b64encode(data).decode()}])["added"],
                             0)
        finally:
            lib.solves.shutdown()

    def test_zip_in_folder(self):
        from analyzer.parsers import decode, load_hands, read_zip
        self.folder.mkdir()
        (self.folder / "archive.zip").write_bytes(make_zip({"a/b/hu.txt": self.betclic.encode("cp1252")}))
        self.assertEqual(len(load_hands([self.folder])), 4)  # un zip copié dans le dossier se lit aussi
        (self.folder / "abime.zip").write_bytes(b"PK pas un zip")
        self.assertEqual(len(load_hands([self.folder])), 4)
        self.assertEqual(decode("é".encode("cp1252")), "é")
        self.assertEqual(decode("é".encode("utf-16")), "é")  # avec son BOM
        with mock.patch("analyzer.parsers.MAX_ZIP_SIZE", 10):
            with self.assertRaises(ValueError):
                read_zip(make_zip({"hu.txt": self.betclic}))


class ServerTest(unittest.TestCase):
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
            self.assertEqual(self.request("GET", "/field/bluffs")[0], 200)
            self.assertEqual(self.request("GET", "/field/joueurs")[0], 200)
            self.assertEqual(self.request("GET", "/field/inconnue")[0], 404)
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
        self.assertIn(b"Merlin", body)
        self.assertEqual(self.request("GET", "/static/app.js")[0], 200)
        status, _, body = self.request("GET", "/api/state")
        self.assertEqual(json.loads(body)["hero"], "Hero")
        self.assertEqual(self.request("GET", "/p/Villain/plan")[0], 200)
        self.assertEqual(self.request("GET", "/moi/bilan")[0], 200)
        self.assertEqual(self.request("GET", "/p/Villain/preflop")[0], 200)
        self.assertEqual(self.request("GET", "/moi/preflop")[0], 200)
        self.assertEqual(self.request("GET", "/moi/mains")[0], 200)
        status, _, body = self.request("GET", "/api/coups?pot=3bp")  # visualiseur : recherche côté serveur
        self.assertEqual((status, [r["id"] for r in json.loads(body)["rows"]]), (200, ["HAND02"]))
        status, _, body = self.request("GET", "/api/coups/HAND02")  # la main complète, pour la rejouer
        self.assertEqual((status, len(json.loads(body)["x"]) > 3), (200, True))
        self.assertEqual(self.request("GET", "/api/coups/INCONNUE")[0], 404)
        self.assertEqual(json.loads(self.request("GET", "/api/coups?adversaire=Villain&reach=x")[2])["total"], 4)
        status, _, body = self.request("GET", "/api/mains?main=K9s&sit=open&pos=BTN")  # d'où vient le résultat
        self.assertEqual((status, json.loads(body)["n"]), (200, 1))
        self.assertEqual(self.request("GET", "/api/mains?main=K9s&sit=nulle")[0], 404)

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

    def test_ranges(self):
        headers = {"Content-Type": "application/json"}
        status, _, body = self.request("POST", "/api/explorateur/ranges", json.dumps({"hand": "HAND01"}), headers)
        state = json.loads(body)
        self.assertEqual((status, state["supported"], state["line"]), (200, True, "BTN open, BB call"))
        save = json.dumps({"hand": "HAND01", "scope": "coup", "ranges": {"BB": "AA,KK"}})
        status, _, body = self.request("POST", "/api/explorateur/ranges/enregistrer", save, headers)
        self.assertEqual((status, json.loads(body)["adjusted"]), (200, "coup"))
        state = json.loads(self.request("POST", "/api/explorateur/etat", json.dumps({"hand": "HAND01"}), headers)[2])
        self.assertEqual(state["adjusted"], "coup")
        for bad in ({"hand": "HAND01", "scope": "coup", "ranges": {"BB": "AXo"}},
                    {"hand": "HAND01", "scope": "partout", "ranges": {"BB": "AA"}},
                    {"hand": "HAND01", "scope": "coup", "ranges": "AA"}):
            status, _, body = self.request("POST", "/api/explorateur/ranges/enregistrer", json.dumps(bad), headers)
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.request("POST", "/api/explorateur/ranges", json.dumps({"hand": "NOPE"}), headers)[0], 404)
        self.assertEqual(self.request("POST", "/api/explorateur/ranges/autre", json.dumps({"hand": "HAND01"}),
                                      headers)[0], 400)
        clear = json.dumps({"hand": "HAND01", "scope": "coup"})
        status, _, body = self.request("POST", "/api/explorateur/ranges/effacer", clear, headers)
        self.assertEqual((status, json.loads(body)["adjusted"]), (200, None))

    def test_studies_page(self):
        status, _, body = self.request("GET", "/etudes")
        self.assertEqual(status, 200)
        self.assertIn("Aucun coup résolu".encode(), body)
        self.assertIn("Heads-up · SRP".encode(), body)
        self.assertIn("0 / 24</b> flops résolus".encode(), body)
        self.assertIn("Résoudre tous les flops heads-up manquants".encode(), body)  # un onglet pour les trois séries
        state = json.loads(self.request("GET", "/api/spots/hu")[2])
        self.assertEqual((state["group"], len(state["families"])), ("hu", 3))
        self.assertEqual(self.request("GET", "/api/spots/inconnu")[0], 404)
        if shutil.which("node"):  # le script de la page doit au moins être du JavaScript valide
            script = body.decode().rsplit("<script>", 1)[1].split("</script>")[0]
            path = Path(self.tmp.name) / "etudes.js"
            path.write_text(script, encoding="utf-8")
            check = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
        headers = {"Content-Type": "application/json"}
        status, _, body = self.request("POST", "/api/etudes/supprimer", json.dumps({"key": "../x"}), headers)
        self.assertEqual((status, json.loads(body)), (200, {"ok": False}))
        status, _, body = self.request("GET", "/api/spots/6max")  # toutes les séries 6-max (sans charts : aucune)
        self.assertEqual((status, json.loads(body)["total"], json.loads(body)["covered"]), (200, 0, 0))
        status, _, body = self.request("POST", "/api/spots/6max/arreter", "{}", headers)
        self.assertEqual((status, json.loads(body)["busy"]), (200, 0))

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

    def test_student_routes(self):
        headers = {"Content-Type": "application/json"}
        status, _, data = self.request("POST", "/api/eleves", json.dumps({"name": "Anna", "pseudo": "Hero"}), headers)
        self.assertEqual((status, json.loads(data)["id"]), (200, "anna"))
        body = json.dumps({"files": [{"name": "s.txt", "content": FIXTURE.read_text(encoding="utf-8")}]})
        status, _, data = self.request("POST", "/api/eleves/anna/import", body, headers)
        self.assertEqual((status, json.loads(data)["added"]), (200, 4))
        job = json.loads(self.request("POST", "/api/eleves/anna/import/lancer", body, headers)[2])
        deadline = time.time() + 20
        while job["state"] == "running" and time.time() < deadline:
            time.sleep(0.05)
            job = json.loads(self.request("GET", f"/api/eleves/anna/import/{job['id']}")[2])
        self.assertEqual((job["state"], job["result"]["added"]), ("done", 0))  # déjà dans sa base
        self.assertEqual(self.request("GET", "/api/eleves/anna/import/inconnu")[0], 404)
        self.assertEqual([s["id"] for s in json.loads(self.request("GET", "/api/eleves")[2])], ["anna"])
        for page in ("leaks", "preflop", "solveur", "spots", "mains"):
            self.assertEqual(self.request("GET", f"/eleve/anna/{page}")[0], 200, page)
        self.assertEqual(self.request("GET", "/api/eleves/anna/mains?main=AA")[0], 200)
        self.assertEqual(json.loads(self.request("GET", "/api/eleves/anna/coups")[2])["total"], 4)
        self.assertEqual(self.request("GET", "/api/eleves/anna/coups/HAND01")[0], 200)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("GET", "/eleve/anna/rapport")
        resp = conn.getresponse()
        report = resp.read()
        conn.close()
        self.assertEqual((resp.status, resp.getheader("Content-Disposition")),
                         (200, 'attachment; filename="leakfinding-anna.html"'))
        self.assertIn(b"Leakfinding de Anna", report)
        self.assertEqual(json.loads(self.request("GET", "/api/eleves/anna/leaks")[2])["ready"], False)
        files = json.loads(self.request("GET", "/api/eleves/anna/fichiers")[2])  # ses historiques : en retirer un
        self.assertEqual([(f["name"], f["hands"], f["removed"]) for f in files], [("s.txt", 4, None)])
        status, _, data = self.request("POST", "/api/eleves/anna/fichiers/retirer", json.dumps({"id": files[0]["id"]}),
                                       headers)
        data = json.loads(data)
        self.assertEqual((status, data["removed"], data["state"]["hands"], data["files"][0]["hands"]), (200, 4, 0, 0))
        status, _, data = self.request("POST", "/api/eleves/anna/fichiers/retablir", json.dumps({"id": files[0]["id"]}),
                                       headers)
        self.assertEqual((status, json.loads(data)["added"]), (200, 4))
        for bad, code in (({"id": "1"}, 400), ({"id": 99999}, 404)):
            self.assertEqual(self.request("POST", "/api/eleves/anna/fichiers/retirer", json.dumps(bad), headers)[0],
                             code, bad)
        view = json.loads(self.request("GET", "/api/eleves/anna/pseudos")[2])  # ses mains par pseudo : en supprimer
        self.assertEqual(([(p["name"], p["hands"]) for p in view["pseudos"]], view["removed"]), ([("Hero", 4)], []))
        status, _, data = self.request("POST", "/api/eleves/anna/pseudos/supprimer", json.dumps({"names": ["Hero"]}),
                                       headers)
        data = json.loads(data)
        self.assertEqual((status, data["removed"], data["state"]["hands"], data["pseudos"]["removed"][0]["name"]),
                         (200, 4, 0, "Hero"))
        status, _, data = self.request("POST", "/api/eleves/anna/pseudos/retablir", json.dumps({"name": "Hero"}),
                                       headers)
        self.assertEqual((status, json.loads(data)["added"]), (200, 4))
        for verb, bad, code in (("supprimer", {"names": "Hero"}, 400), ("supprimer", {"names": ["Personne"]}, 400),
                                ("retablir", {"name": "Personne"}, 404), ("retablir", {"name": 3}, 400)):
            self.assertEqual(self.request("POST", f"/api/eleves/anna/pseudos/{verb}", json.dumps(bad), headers)[0],
                             code, bad)
        self.assertEqual(self.request("GET", "/api/pseudos")[0], 200)
        self.assertEqual(self.request("GET", "/eleve/inconnu/leaks")[0], 404)
        self.assertEqual(self.request("POST", "/api/eleves/inconnu/import", body, headers)[0], 404)
        self.assertEqual(self.request("POST", "/api/eleves", json.dumps({"name": ""}), headers)[0], 400)
        self.assertEqual(self.request("GET", "/moi/leaks")[0], 200)

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
        # en arrière-plan, avec son avancement (la barre de la page Importer)
        status, _, data = self.request("POST", "/api/import/lancer", body, {"Content-Type": "application/json"})
        job = json.loads(data)
        self.assertEqual((status, job["state"] in ("running", "done")), (200, True))
        deadline = time.time() + 20
        while job["state"] == "running" and time.time() < deadline:
            time.sleep(0.05)
            job = json.loads(self.request("GET", f"/api/import/{job['id']}")[2])
        self.assertEqual((job["state"], job["progress"]), ("done", 1.0))
        self.assertEqual(job["result"]["files"][0]["status"], "déjà importé")
        self.assertEqual(self.request("GET", "/api/import/inconnu")[0], 404)
        self.assertEqual(self.request("POST", "/api/import/lancer", "pas du json")[0], 400)


if __name__ == "__main__":
    unittest.main()

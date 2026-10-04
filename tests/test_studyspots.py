import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from analyzer.app.library import Library
from analyzer.app.solves import NeedSession
from analyzer.app.studies import build_studies_page
from analyzer.theory import postflop, sizing, studyspots
from analyzer.theory.preflop import load_solution
from analyzer.theory.studyspots import SRP_FLOPS, TEXTURES, StudySpot, cards_of, flop_texture

FAKE_SOLVER = Path(__file__).parent / "fixtures" / "fake_solver.py"
POSIX = os.name != "nt"


class TextureTest(unittest.TestCase):
    def test_textures(self):
        cases = {"KsKd4c": "Pairé", "AhAsAd": "Pairé", "As8s3s": "Monotone", "7d5d5c": "Pairé",
                 "AcKd9h": "Ace high", "Ks8d3h": "King high", "QhJc9d": "Queen high", "JhTc8d": "Jack high",
                 "Ts5d2h": "Ten high", "9s5d2h": "Low board", "6c4c2d": "Low board"}
        for board, texture in cases.items():
            self.assertEqual(flop_texture(cards_of(board)), texture, board)
        self.assertEqual([studyspots.find_texture(n) for n in ("paire", "ACE HIGH", "Low board", "rien")],
                         ["Pairé", "Ace high", "Low board", None])

    def test_series(self):
        for texture, flops in SRP_FLOPS.items():
            self.assertEqual(len(flops), 3)
            for board in flops:
                self.assertEqual(flop_texture(cards_of(board)), texture, board)
        boards = studyspots.flop_set("srp")
        self.assertEqual((len(boards), len(set(boards))), (24, 24))
        # un flop par texture à tour de rôle : une série interrompue couvre déjà chaque texture
        self.assertEqual([flop_texture(cards_of(b)) for b in boards[:8]], list(TEXTURES))
        self.assertEqual(studyspots.flop_set("srp", ["Monotone"]), list(SRP_FLOPS["Monotone"]))

    def test_parse_ident(self):
        spot = studyspots.parse_ident("spot:srp:KsKd4c")
        self.assertEqual((spot.ident, spot.board, spot.texture), ("spot:srp:KsKd4c", ["Ks", "Kd", "4c"], "Pairé"))
        self.assertEqual(studyspots.parse_ident("spot:3bet:KsKd4c").name, "pot 3bet")
        for bad in ("spot:srp:KsKs4c", "spot:5bet:KsKd4c", "spot:srp:KsKd", "srp:KsKd4c", "spot:srp:KsKd4c5h",
                    "spot:srp:kskd4c", "spot:srp:KsKd4c:x"):
            self.assertIsNone(studyspots.parse_ident(bad), bad)

    def test_tree(self):
        spot = StudySpot("srp", cards_of("KsKd4c"), plan={})  # arbre par défaut (sans tailles choisies)
        request = spot.request(50, 2.0)
        tree = request["spot"]["tree"]
        self.assertEqual((tree["starting_pot"], tree["effective_stack"]), (5.0, 97.5))
        self.assertEqual(tree["oop"][0]["bet"], [])  # la BB ne mène pas au flop (donk)
        self.assertEqual(tree["ip"][0]["bet"], [{"PotPct": 33.0}])
        self.assertEqual(tree["oop"][1]["bet"], [{"PotPct": 75.0}])
        self.assertEqual(request["line"], [])
        self.assertIn("AA", request["spot"]["range_ip"].split(","))
        self.assertNotIn("plan", request)  # sans plan, la requête (et la clé de l'étude) ne change pas
        planned = StudySpot("srp", cards_of("KsKd4c"), plan={"bet:ti:x": [100], "bet:fi:": [75.0, "geo", "a"]})
        self.assertEqual(planned.request()["plan"], {"bet:fi:": [75.0, "geo", "a"], "bet:ti:x": [100.0]})
        self.assertNotEqual(postflop.study_key(planned.request(50, 2.0)), postflop.study_key(request))
        # le nombre de threads ne change pas l'étude
        self.assertEqual(postflop.study_key(spot.request(50, 2.0, threads=4)), postflop.study_key(request))
        result = spot.interpret({"iterations": 50, "exploit_pct": 1.0, "seconds": 3.0})
        self.assertEqual((result["spot"], result["decisions"], result["texture"]), (True, [], "Pairé"))


class ThreeBetTest(unittest.TestCase):
    def test_family_matches_preflop_solution(self):
        node = load_solution().nodes["bb_vs_open"]
        info = studyspots.FAMILIES["3bet"]
        self.assertEqual(node.sizes["raise"], 11.5)
        self.assertEqual((info["pot"], info["stack"]), (2 * 11.5, 100 - 11.5))
        spot = StudySpot("3bet", cards_of("KsKd4c"), plan={})
        tree = spot.request()["spot"]["tree"]
        self.assertEqual(tree["oop"][0]["bet"], [{"PotPct": 33.0}])  # la BB a l'initiative : elle c-bette
        self.assertEqual((spot.pot_bb, spot.stack_bb, spot.name), (23.0, 88.5, "pot 3bet"))
        self.assertIn("AA", spot.request()["spot"]["range_oop"].split(","))

    def test_situations(self):
        sits = {s.key: s for s in sizing.situations("3bet")}
        self.assertEqual(len(sits), 66)
        self.assertEqual([s.key for s in sizing.situations("3bet")[:3]], ["bet:fo:", "raise:fi::0", "bet:fi:"])
        self.assertEqual(sits["bet:fo:"].candidates, [33, 75, "geo2"])  # c-bet
        self.assertEqual(sits["bet:to:o"].candidates, [33, 50, 75, "a"])  # 2e barrel
        self.assertEqual(sits["bet:to:x"].candidates, [33, 75, "geo"])  # c-bet retardée
        self.assertEqual((sits["bet:ro:oo"].candidates, sits["bet:ro:oo"].choose), ([33, 50, 75, "a"], 2))  # 3e barrel
        self.assertEqual(sits["bet:ro:xx"].candidates, [33, 50, 75, "a"])  # probe river
        for key in ("bet:fi:", "bet:ti:o", "bet:ri:ox"):  # stab du bouton
            self.assertEqual((sits[key].candidates, sits[key].choose), ([25, 50, "a"], 1))
        self.assertEqual(sits["raise:fi::0"].candidates, [33, 66, "a"])
        self.assertNotIn("bet:to:i", sits)  # la BB ne mène pas dans le stab payé du bouton
        self.assertEqual(sizing.label("bet:fi:", "3bet"), "Stab flop du BTN")
        self.assertEqual(sizing.sizes_text([33, "geo2"]), "33 % / géo 2 streets")
        self.assertTrue(sizing.plan_text(sizing.initial_plan("3bet"), "3bet").startswith("c-bet 75 % · relance du BTN"))


class FourBetTest(unittest.TestCase):
    def test_family_matches_preflop_solution(self):
        self.assertEqual(load_solution().nodes["sb_vs_3bet"].sizes["raise"], 26)
        info = studyspots.FAMILIES["4bet"]
        self.assertEqual((info["pot"], info["stack"]), (2 * 26.0, 100 - 26.0))
        tree = StudySpot("4bet", cards_of("KsKd4c"), plan={}).request()["spot"]["tree"]
        self.assertEqual(tree["oop"][0]["bet"], [])  # la BB a payé le 4bet : pas de donk

    def test_situations(self):
        sits = {s.key: s for s in sizing.situations("4bet")}
        self.assertEqual(len(sits), 63)
        self.assertEqual(sits["bet:fi:"].candidates, [25, "geo2", "a"])
        for key in ("bet:ti:i", "bet:ti:x", "bet:to:x"):  # 2e barrel, c-bet retardée, probe turn
            self.assertEqual(sits[key].candidates, [25, 50, "a"])
        river = sits["bet:ri:ii"]
        self.assertEqual((river.candidates, river.choose, len(river.options()), river.initial()),
                         ([25, 50, "a"], 2, 3, [25, "a"]))
        self.assertEqual(sits["raise:fo::0"].candidates, [33, "a"])


class SizingRulesTest(unittest.TestCase):
    def test_situations(self):
        sits = {s.key: s for s in sizing.situations()}
        self.assertEqual(len(sits), 63)
        self.assertEqual(sits["bet:fi:"].candidates, [33, 75, "geo"])
        self.assertEqual(sits["bet:ti:i"].candidates, [50, 100, "geo"])  # 2e barrel
        self.assertEqual(sits["bet:ti:x"].candidates, [33, 66, "geo"])  # c-bet retardée
        self.assertEqual(sits["bet:to:x"].candidates, [33, 75, 100, "geo"])  # probe turn
        self.assertEqual((sits["bet:ri:ix"].choose, len(sits["bet:ri:ix"].options())), (2, 10))
        self.assertEqual(sits["raise:ro:ix:0"].candidates, [33, 66, "a"])
        self.assertNotIn("bet:to:i", sits)  # pas de donk de la BB
        self.assertNotIn("bet:ro:xi", sits)
        self.assertEqual(sizing.label("bet:ri:ix"), "Bet/check/bet")
        self.assertEqual(sizing.label("raise:ti:x:1"), "Sur-relance turn du BTN (flop checké)")

    def test_pick_prefers_smaller_on_ties(self):
        self.assertEqual(sizing.pick([33, 75, "geo"], [3.00, 3.05, 3.01]), 1)
        self.assertEqual(sizing.pick([33, 75, "geo"], [3.04, 3.05, 3.00]), 0)  # écart sous EPS
        self.assertEqual(sizing.sample([str(k) for k in range(49)], 4), ["6", "18", "30", "42"])
        self.assertEqual(sizing.sizes_text([100, "geo", "a", 33]), "pot / géo / tapis / 33 %")


@unittest.skipUnless(POSIX, "faux solveur : script exécutable POSIX")
class SolveSpotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        env = {"ANALYZER_HOME": str(self.folder / "home"), "ANALYZER_SOLVER": str(FAKE_SOLVER)}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        # Sans les tailles livrées avec Analyzer : chaque test part d'aucun choix.
        self.shipped = mock.patch.object(studyspots, "shipped_path", lambda family: self.folder / "aucun.json")
        self.shipped.start()

    def tearDown(self):
        self.shipped.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_solve_set_and_library(self):
        log = []
        self.assertEqual(studyspots.solve_set("srp", ["Monotone"], log=log.append, choose=False), 3)
        studies = studyspots.spot_studies()
        self.assertEqual(sorted(studies), sorted(f"spot:srp:{b}" for b in SRP_FLOPS["Monotone"]))
        meta = studies["spot:srp:As8s3s"]
        self.assertEqual((meta["texture"], meta["family"], meta["board"]), ("Monotone", "srp", ["As", "8s", "3s"]))
        titles = [e["title"] for e in meta["summary"]]
        self.assertEqual(titles[1:], ["C-bet du BTN", "BB face à la c-bet"])  # le faux arbre laisse miser la BB
        self.assertEqual(meta["summary"][1]["path"], [{"type": "action", "index": 0}])
        self.assertAlmostEqual(sum(meta["summary"][1]["freqs"]), 1.0, places=6)
        self.assertEqual(studyspots.solve_set("srp", ["Monotone"], log=log.append, choose=False), 0)  # déjà résolus
        self.assertIn("déjà résolu", log[-1])

        lib = Library(self.folder / "mains")
        try:
            series = lib.spot_set("srp")
            self.assertEqual((series["total"], series["done"], series["busy"]), (24, 3, 0))
            ident = "spot:srp:As8s3s"
            state = lib.explorer_state(ident)
            self.assertEqual((state["state"], state["study"], state["live"]), ("done", True, False))
            self.assertEqual((state["meta"]["spot"], state["meta"]["texture"]), (True, "Monotone"))
            with self.assertRaises(NeedSession):  # pas de ligne jouée en cache : il faut ouvrir l'étude
                lib.explorer_node(ident, [])
            job = lib.solve(ident, start=True, force=True)
            self.assertEqual(job["mode"], "load")
            self._wait(lib, job["job"])
            reply = lib.explorer_node(ident, [{"type": "action", "index": 0}])
            self.assertEqual((reply["live"], reply["node"]["player"]), (True, 1))

            page = build_studies_page()
            for text in ("Spots d'étude · SRP", "Monotone", "Moyenne", "C-bet du BTN", "spot:srp:As8s3s",
                         "21 flops manquants"):
                self.assertIn(text, page)

            # référence : synthèses de cet ordinateur exportées, montrées là où rien n'est résolu
            ref_path = studyspots.export_reference("srp", self.folder / "ref.json")
            with mock.patch.object(studyspots, "reference_path", lambda family="srp": ref_path):
                self.assertEqual(sorted(studyspots.reference("srp")), sorted(studies))
                self.assertNotIn("réf.</span>", build_studies_page())  # les études locales passent avant
                for meta in studyspots.spot_studies().values():
                    postflop.delete_study(meta["key"])
                page = build_studies_page()
                self.assertIn("réf.</span>", page)
                self.assertIn("C-bet du BTN", page)
                with mock.patch.object(postflop, "DEFAULT_BETS", (50.0, 75.0, 75.0)):  # autre arbre : ignorée
                    self.assertEqual(studyspots.reference("srp"), {})
            self.assertEqual(lib.spot_set("srp")["done"], 0)

            # un flop hors série, étudié depuis l'explorateur, rejoint sa texture
            other = "spot:srp:Ah7d2c"
            self._wait(lib, lib.solve(other, start=True)["job"])
            self.assertEqual(lib.spot_set("srp")["total"], 25)
            self.assertIn(other, build_studies_page())
        finally:
            lib.solves.shutdown()

    def test_queue_and_cancel(self):
        studyspots.solve_set("srp", ["Monotone"], log=lambda m: None, choose=False)
        lib = Library(self.folder / "mains")
        try:
            with mock.patch.dict(os.environ, {"FAKE_SOLVER_SLEEP": "30"}):
                series = lib.spot_set("srp", start=True)
                self.assertEqual((series["busy"], series["done"]), (21, 3))
                deadline = time.time() + 10
                # sans tailles choisies, chaque flop passe d'abord par le choix des tailles
                while not any(r.get("state") == "running" and r.get("mode") == "choose"
                              for r in lib.spot_set("srp")["rows"]):
                    self.assertLess(time.time(), deadline)
                    time.sleep(0.05)
            self.assertEqual(lib.spot_set("srp", start=True)["busy"], 21)  # pas de doublon
            # une étude enregistrée se rouvre sans attendre la série
            job = lib.solve("spot:srp:As8s3s", start=True, force=True)
            self.assertEqual(job["mode"], "load")
            self._wait(lib, job["job"])
            self.assertEqual(lib.spot_set("srp")["busy"], 21)
            stopped = lib.spot_cancel("srp")
            self.assertLessEqual(stopped["busy"], 1)  # celle en cours s'arrête dans la seconde
            deadline = time.time() + 10
            while lib.spot_set("srp")["busy"] and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(lib.spot_set("srp")["busy"], 0)
            self.assertLess(time.time(), deadline)
            with self.assertRaises(KeyError):
                lib.spot_set("inconnue")
        finally:
            lib.solves.shutdown()

    def test_choose_sizes(self):
        # Le faux solveur donne plus d'EV aux tailles proches de 40 % (BB) et 70 % (bouton) ; seule la ligne
        # « flop checké » mène à la turn ; les sur-relances y sont rares.
        log = []
        result = studyspots.choose_sizes("srp", "KsKd4c", log.append)
        plan, report = result["plan"], result["report"]
        self.assertEqual(plan["bet:fi:"], [75])
        self.assertEqual(plan["raise:fo::0"], [33])
        self.assertEqual(plan["raise:fi::1"], [66])
        self.assertEqual((plan["bet:ti:x"], plan["bet:to:x"]), ([66], [33]))
        self.assertEqual((plan["bet:ri:xx"], plan["bet:ro:xx"]), ([50, 75], [50, 75]))
        self.assertEqual(report["bet:fi:"]["method"], "arbre complet")
        self.assertEqual(report["bet:ti:x"]["cards"], 8)
        self.assertEqual((report["raise:ti:x:1"]["method"], plan["raise:ti:x:1"]), ("rare", [33]))
        self.assertNotIn("bet:ti:i", report)  # ligne absente de l'arbre : choix de départ gardé
        self.assertEqual(plan["bet:ti:i"], [100])
        self.assertTrue(studyspots.selection_path("srp", "KsKd4c").is_file())
        # le spot utilise désormais ces tailles
        spot = StudySpot("srp", cards_of("KsKd4c"))
        self.assertEqual(spot.request()["plan"]["bet:fi:"], [75.0])
        self.assertIn("c-bet 75 %", spot.menu_text())
        self.assertEqual(StudySpot("srp", cards_of("KsKd4c"), plan={}).request().get("plan"), None)
        self.assertEqual(studyspots.load_selection("srp", "KsKd4c")["source"], "local")
        # fichier livré : les choix de cet ordinateur, rassemblés
        shipped = studyspots.export_selections("srp", self.folder / "livre.json")
        self.shipped.stop()
        with mock.patch.object(studyspots, "shipped_path", lambda family: shipped):
            studyspots.selection_path("srp", "KsKd4c").unlink()
            self.assertEqual(studyspots.load_selection("srp", "KsKd4c")["source"], "livré")
            self.assertEqual(StudySpot("srp", cards_of("KsKd4c")).plan["bet:fi:"], [75])
        self.shipped.start()

    def test_choose_sizes_3bet(self):
        # Même faux solveur : 40 % pour la BB, 70 % pour le bouton ; seul le flop checké mène à la turn.
        result = studyspots.choose_sizes("3bet", "KsKd4c", lambda m: None)
        plan, report = result["plan"], result["report"]
        self.assertEqual((plan["bet:fo:"], plan["raise:fi::0"], plan["bet:fi:"]), ([33], [66], [50]))
        self.assertEqual(len([e for e in report.values() if e["method"] == "arbre complet"]), 6)
        self.assertEqual((plan["bet:to:x"], plan["bet:ti:x"]), ([33], [50]))
        self.assertEqual((plan["bet:ro:xx"], plan["bet:ri:xx"]), ([33, 50], [50]))
        spot = StudySpot("3bet", cards_of("KsKd4c"))
        self.assertEqual(spot.request()["plan"]["bet:fo:"], [33.0])
        self.assertTrue(spot.menu_text().startswith("c-bet 33 %"))
        self.assertIn("Spots d'étude · pot 3bet", build_studies_page())

    def test_choose_sizes_4bet(self):
        plan = studyspots.choose_sizes("4bet", "KsKd4c", lambda m: None)["plan"]
        self.assertEqual((plan["bet:fi:"], plan["raise:fo::0"], plan["raise:fi::1"]), ([25], [33], [33]))
        self.assertEqual((plan["bet:to:x"], plan["bet:ti:x"]), ([50], [50]))
        self.assertEqual((plan["bet:ro:xx"], plan["bet:ri:xx"]), ([25, 50], [25, 50]))
        self.assertIn("Spots d'étude · pot 4bet", build_studies_page())

    def test_app_chooses_then_solves(self):
        lib = Library(self.folder / "mains")
        try:
            ident = "spot:srp:Jh9h5h"
            job = lib.solves.choose_and_solve(ident, lambda job: lib._choose("srp", "Jh9h5h", job),
                                              lambda: StudySpot("srp", cards_of("Jh9h5h")))
            self.assertEqual(job["mode"], "choose")
            deadline = time.time() + 60
            while lib.solves.get(job["job"])["state"] in ("waiting", "running") and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(lib.solves.get(job["job"])["state"], "done")
            row = next(r for r in lib.spot_set("srp")["rows"] if r["id"] == ident)
            self.assertEqual((row["done"], row["sizes"]), (True, True))
            self.assertIn("C-bet du BTN", build_studies_page())
        finally:
            lib.solves.shutdown()

    @staticmethod
    def _wait(lib, key):
        deadline = time.time() + 20
        while lib.solves.get(key)["state"] in ("waiting", "running") and time.time() < deadline:
            time.sleep(0.05)
        assert lib.solves.get(key)["state"] == "done", lib.solves.get(key)


if __name__ == "__main__":
    unittest.main()

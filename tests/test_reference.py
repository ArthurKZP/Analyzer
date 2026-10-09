"""Joueurs de référence : l'étude d'un bon joueur dont tu as importé les historiques (toutes ses cartes connues) et la
comparaison avec toi."""
import http.client
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from analyzer import field, reference, settings
from analyzer.app.library import Library, UnknownPlayer
from analyzer.app.server import start
from analyzer.parsers import parse_text
from analyzer.stats import Ratio

try:
    from .base import IsolatedHome, close_storage
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome, close_storage

SITES = Path(__file__).parent / "sites"
FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"


def as_model(text: str) -> str:
    """Les mêmes mains jouées par un autre héros (« Modele »), sous d'autres numéros."""
    text = text.replace("Hero", "Modele")
    text = re.sub(r"\[([A-Z ]+) Modele\]", r"[\1 Hero]", text)  # l'étiquette du héros chez Betclic reste « Hero »
    return text.replace("Hand #2000000000", "Hand #2100000000").replace("HAND0", "MODL0")


def write_two_heroes(folder: Path) -> None:
    """Tes mains (heads-up Betclic, 6-max PokerStars) et celles d'un autre héros, importées chez toi."""
    for src in (SITES / "pokerstars.txt", FIXTURE):
        text = src.read_text(encoding="utf-8")
        (folder / src.name).write_text(text, encoding="utf-8")
        (folder / f"modele_{src.name}").write_text(as_model(text), encoding="utf-8")


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.hands = parse_text((SITES / "pokerstars.txt").read_text(encoding="utf-8"))

    def test_result_splits_showdown(self):
        r = reference.result(self.hands, "Hero")
        # deux petites blindes couchées (−0,4 bb chacune), une c-bet qui passe (+2,8 bb) : rien à l'abattage
        self.assertEqual(r.hands, 3)
        self.assertAlmostEqual(r.net_bb, 2.0)
        self.assertAlmostEqual(r.sd_bb, 0.0)
        self.assertAlmostEqual(r.nosd100, 200 / 3)
        winner = reference.result(self.hands[:1], "Revenant")  # il gagne à l'abattage
        self.assertAlmostEqual(winner.sd_bb, 6.6)
        self.assertEqual(set(reference.by_position(self.hands, "Hero")), {"SB", "BTN"})

    def test_rows(self):
        row = reference.Row("vpip", "VPIP", "Avant le flop", Ratio(30, 100), Ratio(20, 100), (20, 28))
        self.assertAlmostEqual(row.gap, -10.0)
        self.assertAlmostEqual(row.z, -1.63, places=2)
        self.assertFalse(row.clear)  # le hasard l'explique encore (test à 90 %)
        self.assertEqual(row.closer(), "lui")  # lui dans le repère, toi au-dessus
        clear = reference.Row("pfr", "PFR", "Avant le flop", Ratio(40, 100), Ratio(20, 100), (16, 23))
        self.assertTrue(clear.clear)
        few = reference.Row("pfr", "PFR", "Avant le flop", Ratio(4, 10), Ratio(0, 10))
        self.assertFalse(few.clear)  # moins de 20 occasions chacun
        self.assertIsNone(few.closer())
        self.assertEqual(reference.gaps([row, clear, few]), [clear])
        ring_rows = reference.compare_ring({"vpip": Ratio(30, 100)}, {"vpip": Ratio(20, 100)})
        vpip = next(r for r in ring_rows if r.key == "vpip")
        self.assertEqual((vpip.label, vpip.section, vpip.ref), ("VPIP", "Avant le flop", (20, 28)))
        self.assertEqual(next(r for r in ring_rows if r.key == "cbet_river").label, "3e barrel")

    def test_lines_know_every_card(self):
        zoom = self.hands[2]  # sa c-bet avec As-Dame sur K-7-2 : un bluff, tout le monde se couche
        (bet,) = field.player_bets([zoom], ["Hero"])
        self.assertIsNone(bet.intent)  # pas d'abattage : l'étude du field ne la lit pas
        (bet,) = field.player_bets([zoom], ["Hero"], all_cards=True)
        self.assertEqual((bet.label, bet.intent), ("C-bet", "bluff"))
        self.assertTrue(reference.passed(bet))
        (called,) = field.player_bets(self.hands[:1], ["Revenant"], all_cards=True)[:1]
        self.assertFalse(reference.passed(called))  # sa mise flop est payée
        with mock.patch.object(reference, "MIN_LINE", 1):
            rows = reference.lines([zoom], "Hero", self.hands[:1], "Revenant")
        cbet = next(r for r in rows if r.label == "C-bet")
        # la sienne (un bluff qui passe) et celle de l'autre joueur (une paire moyenne, payée), dans la même ligne
        self.assertEqual((cbet.street, cbet.his.count, cbet.his.bluff, cbet.his.passes), ("flop", 1, 100.0, 100.0))
        self.assertEqual((cbet.mine.count, cbet.mine.value, cbet.mine.passes), (1, 100.0, 0.0))
        totals = reference.street_totals(rows)
        self.assertEqual(totals["flop"][0].count, 1)
        self.assertEqual(reference.examples(rows, ("bluff", "semi")), [cbet.his.bets[0]])
        self.assertEqual(reference.lines([zoom], "Hero", [], "Revenant"), [])  # moins de MIN_LINE mises


class LibraryReferenceTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        write_two_heroes(self.folder)

    def test_reference_player(self):
        lib = Library(self.folder)
        try:
            # deux héros marqués dans tes historiques : réunis d'office sous ton nom
            self.assertEqual(len(lib.hands) + len(lib.ring), 14)
            view = lib.references_view()
            self.assertEqual({c["name"]: c["mine"] for c in view["candidates"]}, {"Hero": True, "Modele": True})
            with self.assertRaises(ValueError):
                lib.create_reference("Le modèle", ["Inconnu"])  # pas un héros de tes historiques
            with self.assertRaises(ValueError):
                lib.create_reference("Villain", ["Modele"])  # le nom d'un joueur de tes mains
            out = lib.create_reference("Le modèle", ["Modele"])
            self.assertEqual(out["id"], "le-modele")
            self.assertIn("Modele", settings.load()["hero_excluded"])
            # ses mains sortent des tiennes et sont étudiées à part, sous son nom
            self.assertEqual((lib.hero, len(lib.hands), len(lib.ring)), ("Hero", 4, 3))
            ref = lib.reference("le-modele")
            self.assertEqual((ref.hero, len(ref.hands), len(ref.ring)), ("Le modèle", 4, 3))
            self.assertTrue(all(h.hero == "Le modèle" for h in ref.hands + ref.ring))
            summary = out["references"][0]
            self.assertEqual((summary["name"], summary["hands"], summary["ring_hands"], summary["pseudos"][0]["name"]),
                             ("Le modèle", 4, 3, "Modele"))
            self.assertEqual([c["name"] for c in out["candidates"]], ["Hero"])
            with self.assertRaises(ValueError):
                lib.create_reference("Le modèle", ["Hero"])  # deux joueurs de référence du même nom
            # ses pages : la comparaison, sa value et ses bluffs, son jeu analysé comme le tien
            page = lib.reference_page("le-modele", "comparaison", "HU")
            self.assertIn("Ce qu&#x27;il fait autrement", page.replace("'", "&#x27;"))
            self.assertIn("Toutes les fréquences", page)
            self.assertIn("Toutes les fréquences", lib.reference_page("le-modele", "comparaison", "ring"))
            self.assertIn("Value et bluffs", lib.reference_page("le-modele", "lignes", "ring"))
            self.assertIn("analysé comme le tien", lib.reference_page("le-modele", "bilan", "HU"))
            self.assertIn("analysé comme le tien", lib.reference_page("le-modele", "tables"))
            with self.assertRaises(KeyError):
                lib.reference_page("le-modele", "leaks")
            with self.assertRaises(UnknownPlayer):
                lib.reference("inconnu")
            self.assertEqual(lib.find_hand(ref.hands[0].hand_id)[1], "Le modèle")  # l'explorateur ouvre ses mains
            # Paramètres › Toi : son pseudo n'est plus le tien
            row = next(p for p in lib.settings_view()["pseudos"] if p["name"] == "Modele")
            self.assertEqual((row["included"], row["reference"]), (False, "Le modèle"))
            # retiré : ses pseudos restent hors des tiens (à recocher dans Paramètres › Toi)
            out = lib.remove_reference("le-modele")
            self.assertEqual(out["references"], [])
            self.assertEqual((len(lib.hands), len(lib.ring)), (4, 3))
            with self.assertRaises(UnknownPlayer):
                lib.remove_reference("le-modele")
        finally:
            lib.solves.shutdown()

    def test_settings_check(self):
        self.assertEqual(settings.check({"references": [{"name": " Le   pro ", "pseudos": ["b", "a", "a"]}]}),
                         {"references": [{"name": "Le pro", "pseudos": ["a", "b"]}]})
        for bad in ([{"name": "", "pseudos": ["a"]}], [{"name": "x", "pseudos": []}], [{"name": "x"}], "x",
                    [{"name": "x", "pseudos": ["a"]}, {"name": "X", "pseudos": ["b"]}],
                    [{"name": "x", "pseudos": ["a"]}, {"name": "y", "pseudos": ["a"]}]):
            with self.assertRaises(ValueError, msg=bad):
                settings.check({"references": bad})
        self.assertEqual(settings.reference_id("Le pro du 6-max"), "le-pro-du-6-max")


class ServerReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        write_two_heroes(Path(cls.tmp.name))
        cls.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(Path(cls.tmp.name) / "home"),
                                               "ANALYZER_SOLVER": str(Path(cls.tmp.name) / "absent")})
        cls.env.start()
        cls.library = Library(cls.tmp.name)
        cls.server = start(cls.library, port=0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.library.solves.shutdown()
        close_storage()
        cls.env.stop()
        cls.tmp.cleanup()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_routes(self):
        status, body = self.request("GET", "/api/references")
        self.assertEqual((status, json.loads(body)["references"]), (200, []))
        status, body = self.request("POST", "/api/references", {"name": "Le modèle", "pseudos": ["Inconnu"]})
        self.assertEqual(status, 400)
        self.assertIn("héros", json.loads(body)["error"])
        status, body = self.request("POST", "/api/references", {"name": "Le modèle", "pseudos": ["Modele"]})
        self.assertEqual((status, json.loads(body)["id"]), (200, "le-modele"))
        for page in ("comparaison", "lignes", "bilan", "tables", "preflop", "mains"):
            self.assertEqual(self.request("GET", f"/reference/le-modele/{page}")[0], 200, page)
        self.assertEqual(self.request("GET", "/reference/le-modele/comparaison?format=ring")[0], 200)
        self.assertEqual(self.request("GET", "/reference/le-modele/leaks")[0], 404)
        self.assertEqual(self.request("GET", "/reference/inconnu/comparaison")[0], 404)
        self.assertEqual(self.request("GET", "/api/references/le-modele/mains?fmt=ring&main=AQo")[0], 200)
        status, body = self.request("POST", "/api/references/le-modele/retirer")
        self.assertEqual((status, json.loads(body)["references"]), (200, []))
        self.assertEqual(self.request("POST", "/api/references/le-modele/retirer")[0], 404)
        self.assertEqual(self.request("GET", "/reference/le-modele/comparaison")[0], 404)


if __name__ == "__main__":
    unittest.main()

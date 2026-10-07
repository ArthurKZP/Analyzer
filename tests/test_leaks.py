import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer import leaks, students
from analyzer.app.leaks_page import build_leaks_page
from analyzer.parsers import load_hands
from analyzer.stats import Ratio

FIXTURES = Path(__file__).parent / "fixtures"


class StudentsTest(unittest.TestCase):
    def test_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": tmp}):
            paul = students.create("Paul Élève", "PaulPoker")
            self.assertEqual((paul["id"], paul["pseudo"]), ("paul-eleve", "PaulPoker"))
            self.assertEqual(students.create("Paul Élève")["created"], paul["created"])  # déjà là : le même
            students.create("Anna")
            self.assertEqual([s["name"] for s in students.all_students()], ["Anna", "Paul Élève"])
            self.assertIsNone(students.get("../etudes"))  # jamais un chemin hors du dossier des élèves
            with self.assertRaises(ValueError):
                students.create("   ")


class StatTest(unittest.TestCase):
    def stat(self, hits, opps, ref=0.4, fragile=False):
        return leaks.Stat("k", "Préflop", "Bouton : open", "aggr", {"all": Ratio(hits, opps), "reg": Ratio(hits, opps),
                                                                   "rec": Ratio()}, ref, fragile=fragile, per100=40)

    def test_verdict(self):
        self.assertEqual(self.stat(60, 100).verdict(), ("plus", "solide"))
        self.assertEqual(self.stat(6, 10).verdict(), ("plus", "indicatif"))  # net, mais 10 occasions
        self.assertEqual(self.stat(60, 100, fragile=True).verdict(), ("plus", "indicatif"))  # repère sur un flop
        self.assertIsNone(self.stat(42, 100).verdict())
        self.assertIsNone(self.stat(5, 8).verdict())
        self.assertIsNone(self.stat(60, 100).verdict("rec"))

    def test_rank(self):
        group = {"family": "srp", "key": "face:fo::1", "label": "Face à : C-bet du BTN", "n": 10, "known": 10,
                 "errors": 4, "lost": 3.0, "observed": {"fold": 6, "passive": 4, "aggressive": 0},
                 "expected": {"fold": 3.0, "passive": 6.0, "aggressive": 1.0},
                 "variance": {"fold": 2.1, "passive": 2.4, "aggressive": 0.9}}
        digest = {"hand": "H1", "family": "srp", "decisions": [
            {"who": "H", "key": "face:fo::1", "d": 2, "ev_loss": 1.2, "frequency": 0.0}]}
        ranked = leaks.rank([self.stat(60, 100), self.stat(6, 10)], [group], analyzed=20, digests=[digest])
        self.assertEqual([x.confidence for x in ranked], ["solide", "solide", "indicatif"])
        solver = next(x for x in ranked if x.source == "solveur")
        self.assertEqual(solver.example, ("H1", 2, 1.2))
        self.assertIn("3,0 bb perdus en 10 décisions", solver.evidence)
        self.assertIn("Tu foldes trop ici", solver.advice)
        preflop = next(x for x in ranked if x.source == "préflop")
        self.assertEqual(preflop.advice, "Trop large : resserre (onglet Préflop, les mains concernées).")


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = [h for h in load_hands([FIXTURES]) if len(h.seats) == 2]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_build_and_page(self):
        report = leaks.build(self.hands, "Hero", {"Villain": {"kind": "reg"}})
        self.assertEqual((report.hands, report.scope_hands["reg"], report.scope_hands["rec"]), (4, 4, 0))
        labels = {s.label: s for s in report.stats}
        open_ = labels["Bouton : open"]
        self.assertTrue(0 < open_.reference < 1)  # repère de la solution préflop
        self.assertEqual(open_.ratios["reg"].opps, open_.ratios["all"].opps)
        self.assertIn("BTN : C-bet flop", labels)  # HAND01 : c-bet en pot simple
        self.assertEqual(leaks.line_of(next(h for h in self.hands if h.hand_id == "HAND01"), "Hero"), "SRP · BTN · flop")
        self.assertTrue(report.picks["reg"])
        summary = leaks.summary(report)
        self.assertEqual((summary["joueur"], summary["mains_par_type"]["reg"]), ("Hero", 4))

        rec = leaks.build(self.hands, "Hero", {"Villain": {"kind": "rec"}})
        self.assertEqual((rec.scope_hands["reg"], rec.scope_hands["rec"], rec.picks["reg"]), (0, 4, []))
        self.assertTrue(all(p.spot is None for p in rec.picks["rec"]))  # contre un récréatif, pas de solveur
        self.assertEqual(rec.leaks, [])  # seules les mains contre réguliers font des leaks

        page = build_leaks_page(report, api="/api/leaks", pages="/moi", name="Paul",
                                opponents=[{"name": "Villain", "hands": 4, "net_bb": 1.0, "kind": "reg", "source": "défaut",
                                            "suggestion": None}])
        for text in ("Leakfinding de Paul", "Les leaks à travailler", "Ses stats face à la théorie", "Mains à revoir",
                     "Télécharger le rapport", 'data-api="/api/leaks"', 'data-name="Villain"'):
            self.assertIn(text, page)
        alone = build_leaks_page(report, api="/api/leaks", pages="/moi", standalone=True)
        self.assertNotIn("lk-run", alone)
        self.assertNotIn('class="open"', alone)  # pas de lien vers l'application dans le rapport envoyé


if __name__ == "__main__":
    unittest.main()

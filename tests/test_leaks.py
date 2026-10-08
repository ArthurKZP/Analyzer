import os
import shutil
import subprocess
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

    def test_tolerances(self):
        # près de 0 %, quelques points font un gros écart : un open à 23 % au lieu de 16 %, sur 500 occasions
        low = leaks.Stat("k", "Préflop", "UTG : open", "aggr", {"all": Ratio(113, 500)}, 0.158)
        self.assertEqual(low.verdict("all"), ("plus", "solide"))
        # repère calculé sur les mêmes cartes (charts) : la tolérance est moitié moindre
        exact = leaks.Stat("k", "Préflop", "BB : fold", "fold", {"all": Ratio(283, 389)}, 0.656, exact=True)
        self.assertEqual(exact.verdict("all"), ("plus", "solide"))
        self.assertIsNone(leaks.Stat("k", "Préflop", "BB : fold", "fold", {"all": Ratio(283, 389)}, 0.656).verdict("all"))
        # limper 2 fois sur 500 quand les charts ne limpent jamais : rien à dire
        self.assertIsNone(leaks.Stat("k", "Préflop", "SB : limp", "limp", {"all": Ratio(2, 500)}, 0.0, exact=True)
                          .verdict("all"))
        # sans théorie, une fourchette (repère indicatif) : écart dès qu'on en sort nettement
        band = lambda hits: leaks.Stat("k", "Préflop", "UTG : open", "aggr", {"all": Ratio(hits, 100)}, None,  # noqa: E731
                                       band=(0.14, 0.18))
        self.assertEqual(band(30).verdict("all"), ("plus", "solide"))
        self.assertIsNone(band(19).verdict("all"))
        self.assertIsNone(band(16).verdict("all"))
        self.assertEqual((band(30).gap("all"), band(30).ref_text()), (0.12, "un repère de 14 à 18 %"))

    def test_top_gaps(self):
        flop = leaks.Stat("a", "SRP", "BTN : C-bet flop", "bet", {"reg": Ratio(30, 40)}, 0.5, per100=10, stake=2.0)
        river = leaks.Stat("b", "SRP", "BB : Fold face au 3e barrel", "fold", {"reg": Ratio(30, 40)}, 0.5, per100=10,
                           stake=8.0)
        preflop = leaks.Stat("c", "Préflop", "Bouton : open", "aggr", {"reg": Ratio(38, 40)}, 0.82, per100=30)
        loose = leaks.Stat("d", "Préflop", "BB : 3bet", "aggr", {"reg": Ratio(6, 10)}, 0.3, per100=50)
        found = leaks.top_stat_gaps([flop, preflop, loose, river], "reg")
        # les solides d'abord, la river avant le flop (ce que la décision met en jeu), l'indicatif en dernier
        self.assertEqual([(s.key, conf) for s, _, conf in found],
                         [("b", "solide"), ("a", "solide"), ("c", "solide"), ("d", "indicatif")])
        self.assertEqual(river.weight("reg"), 10 * 0.25 * 8.0 * leaks.MISTAKE_COST)

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

        opponents = [{"name": "Villain", "hands": 4, "net_bb": 1.0, "bb100": 25.0, "last": "02/10/2026", "kind": "reg",
                      "source": "défaut", "suggestion": None},
                     {"name": "Joueur", "hands": 2, "net_bb": -3.0, "bb100": -150.0, "last": "01/10/2026", "kind": "rec",
                      "source": "toi", "suggestion": "rec"}]
        page = build_leaks_page(report, api="/api/leaks", pages="/moi", name="Paul", opponents=opponents,
                                formats=[("HU", 4)])
        for text in ("Leakfinding de Paul", "Les leaks à travailler", "Ses stats face à la théorie", "Mains à revoir",
                     "Télécharger le rapport", 'data-api="/api/leaks"', 'data-name="Villain"', 'data-query=""',
                     "Le détail : toutes ses stats", 'class="opp-q"', 'data-kind="rec" data-hands="2" data-net="-3.00"',
                     'data-last="2026-10-02"'):
            self.assertIn(text, page)
        self.assertNotIn('class="lk-fmt"', page)  # un seul format : pas de choix
        self.assertLess(page.index("Ses stats face à la théorie"), page.index("Le détail : toutes ses stats"))
        alone = build_leaks_page(report, api="/api/leaks", pages="/moi", standalone=True)
        self.assertNotIn("lk-run", alone)
        self.assertNotIn('class="open"', alone)  # pas de lien vers l'application dans le rapport envoyé
        if shutil.which("node"):  # les scripts de la page sont du JavaScript valide
            script = page.rsplit("<script>", 1)[1].split("</script>")[0]
            path = Path(self.tmp.name) / "leaks.js"
            path.write_text(script, encoding="utf-8")
            check = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)


class SelfReportTest(unittest.TestCase):
    """Le bilan : les écarts les plus importants d'abord, puis le détail, puis les adversaires (recherche, tri)."""

    def test_priorities(self):
        from analyzer.insights import STAT_DEFS, Finding
        from analyzer.selfreport import priorities

        def finding(key, hits, opps, strong):
            stat = STAT_DEFS[key]
            return Finding(stat, Ratio(hits, opps), "haut" if 100 * hits / opps > stat.ref[1] else "bas", strong,
                           stat.high or stat.low)
        items = [finding("sb_first.raise", 358, 398, False), finding("sb_first.fold", 40, 398, False),
                 finding("vs_bet_river.fold", 61, 82, True), finding("vs_bet_turn.fold", 55, 96, False),
                 finding("xr_flop", 22, 107, False)]
        with mock.patch("analyzer.selfreport.findings", return_value=items):
            keys = [f.stat.key for f in priorities(None, 793)]
        # le net d'abord ; la river et la turn pèsent plus que l'open ; l'open et le fold d'entrée disent la même chose :
        # l'open (plus parlant) prend la place, le fold d'entrée passe après
        self.assertEqual(keys, ["vs_bet_river.fold", "vs_bet_turn.fold", "sb_first.raise", "xr_flop", "sb_first.fold"])

    def test_bilan_order_and_opponents(self):
        from analyzer.selfreport import build_self_report
        from analyzer.stats import analyze
        hands = [h for h in load_hands([FIXTURES]) if len(h.seats) == 2]
        page = build_self_report(hands, analyze(hands), "Hero", embed=True,
                                 kinds={"Villain": {"kind": "reg", "source": "toi"}})
        order = [page.index(t) for t in ("Tes écarts les plus importants", "Tes statistiques", "Résultats par adversaire")]
        self.assertEqual(order, sorted(order))
        self.assertIn('data-name="Villain" data-kind="reg"', page)
        self.assertNotIn('class="opp-q"', page)  # un seul adversaire : pas de barre de recherche


if __name__ == "__main__":
    unittest.main()

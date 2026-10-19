import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from types import SimpleNamespace

from analyzer import handplay, leak_examples, leakcheck, leaks, students
from analyzer.app.leaks_page import _items, build_leaks_page, leaks_html
from analyzer.parsers import load_hands
from analyzer.stats import Ratio
from analyzer.theory import review

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

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

    def test_sized_reference(self):
        """Une autre stratégie théorique n'est pas une fuite : sur-relancer gros et rarement, quand le solveur
        sur-relance petit et souvent."""
        def reraise():
            return leaks.Stat("srp:reraise", "SRP", "BTN : Sur-relance face à une relance", "raise",
                              {"all": Ratio(5, 50), "reg": Ratio(5, 50), "rec": Ratio()}, 0.47)
        stat = reraise()
        self.assertEqual(stat.verdict(), ("moins", "solide"))
        leaks.sized(stat, 0.33, [1.64] * 4, "raise")  # ses sur-relances : 164 % du pot après le call, le solveur 33 %
        self.assertIsNone(stat.verdict())  # à sa taille, le solveur sur-relance 9 % : comme lui
        self.assertEqual((stat.raw, stat.ref_text()), (0.47, "9 % pour le solveur à ta taille"))
        self.assertIn("le solveur le fait 47 % du temps à 33 % du pot ; tes relances font 164 % du pot en moyenne "
                      "(4 fois)", stat.size_note())
        few = reraise()
        leaks.sized(few, 0.33, [1.64] * 2, "raise")  # deux sur-relances : trop peu pour dire sa taille
        self.assertEqual((few.reference, few.sizing, few.ref_text()), (0.47, None, "47 % pour le solveur"))

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


class LinksTest(unittest.TestCase):
    @staticmethod
    def stat(key, section, label, kind, hits, ref):
        return leaks.Stat(key, section, label, kind, {"all": Ratio(hits, 100), "reg": Ratio(hits, 100), "rec": Ratio()},
                          ref, per100=20)

    def test_preflop_explains_the_flop(self):
        defend = self.stat("bb_vs_open.fold", "Préflop", "BB : fold face à l'open", "fold", 20, 0.40)  # trop large
        folds = self.stat("srp:fold_flop", "SRP", "BB : Fold face à la c-bet", "fold", 50, 0.33)  # folde trop
        turn = self.stat("srp:fold_turn", "SRP", "BB : Fold face au 2e barrel", "fold", 40, 0.40)  # comme la théorie
        folds.per100 = 80  # plus lourd : passerait devant sans le lien
        ranked = leaks.rank([folds, defend, turn], [], analyzed=0)
        self.assertEqual([x.key for x in ranked], ["bb_vs_open.fold", "srp:fold_flop"])  # la cause d'abord
        effect = ranked[1]
        self.assertEqual([link.cause.key for link in effect.causes], ["bb_vs_open.fold"])
        self.assertEqual(effect.causes[0].text(),
                         "Lié à « Préflop · BB : fold face à l'open » (moins que la théorie) : ta range est plus "
                         "faible à la street suivante : folder plus en découle ; corrige d'abord celui-là.")
        self.assertEqual(ranked[0].effects, ["SRP · BB : Fold face à la c-bet"])
        self.assertIn("Lié à « Préflop", effect.verified)
        page = _items(ranked, "/moi", True)
        self.assertIn("↳ Sans doute une conséquence de « Préflop · BB : fold face à l&#x27;open » : corrige d'abord "
                      "celui-là.", page)
        self.assertIn('<summary>Le détail : fréquence et preuves</summary>', page)  # le détail, replié
        self.assertIn('<span class="v">20 % <span class="n">sur 100 occasions</span></span>', page)
        self.assertIn("Entraîne sans doute aussi : « SRP · BB : Fold face à la c-bet ».", page)
        # dans l'autre sens (défend trop serré, folde trop face à la c-bet), rien ne les relie
        tight = self.stat("bb_vs_open.fold", "Préflop", "BB : fold face à l'open", "fold", 60, 0.40)
        self.assertEqual(leaks.links([tight, folds]), {})

    def test_streets_and_ring(self):
        calls = self.stat("srp_ip:defenseur:fold_flop", "SRP", "En défense : Fold face à la c-bet", "fold", 20, 0.33)
        turn = self.stat("srp_ip:defenseur:fold_turn", "SRP", "En défense : Fold face au 2e barrel", "fold", 60, 0.40)
        other = self.stat("srp_oop:defenseur:fold_turn", "SRP", "En défense : Fold face au 2e barrel", "fold", 60, 0.40)
        wide = self.stat("pre:vs_open:BB:call", "Préflop · face à une ouverture", "BB : call", "call", 50, 0.30)
        found = leaks.links([calls, turn, other, wide])
        # même structure seulement d'une street à la suivante ; le call préflop trop large, dans toutes les structures
        self.assertEqual({k: [x.cause.key for x in v] for k, v in found.items()},
                         {"srp_ip:defenseur:fold_turn": ["srp_ip:defenseur:fold_flop"]})
        folds = self.stat("srp_oop:defenseur:fold_flop", "SRP", "En défense : Fold face à la c-bet", "fold", 60, 0.33)
        found = leaks.links([wide, folds])
        self.assertEqual([x.cause.key for x in found["srp_oop:defenseur:fold_flop"]], ["pre:vs_open:BB:call"])


class CategoryTest(unittest.TestCase):
    @staticmethod
    def leak(source, key, title="", confidence="solide"):
        return leaks.Leak(title or key, "preuve", "conseil.", source, confidence, 1.0, key=key, direction="plus")

    def test_categories(self):
        found = [self.leak("postflop", "srp:cbet"), self.leak("préflop", "sb_first.raise", "Préflop · Bouton : open"),
                 self.leak("postflop", "srp:fold_flop"), self.leak("postflop", "srp_ip:defenseur:raise"),
                 self.leak("solveur", "solveur:srp:face:fo::1"), self.leak("solveur", "solveur:srp:bet:fi:"),
                 self.leak("solveur", "solveur:inconnue:bet:fi:"), self.leak("postflop", "srp_oop:agresseur:barrel")]
        self.assertEqual([leaks.category(x) for x in found], ["initiative", "preflop", "defense", "defense", "defense",
                                                              "initiative", "postflop", "initiative"])
        groups = leaks.by_category(found)  # dans l'ordre de leur leak le plus important
        self.assertEqual([(c[0], [k for k, _ in items]) for c, items in groups],
                         [("initiative", [1, 6, 8]), ("preflop", [2]), ("defense", [3, 4, 5]), ("postflop", [7])])


class ExamplesTest(unittest.TestCase):
    @staticmethod
    def play(combo, cards, action, theory, ev=-1.0, index=3, position="BTN", situation="open"):
        hand = SimpleNamespace(hand_id=f"H-{combo}", hole_cards={"Hero": cards}, board=[])
        return handplay.Played(hand, combo, position, ev, ev,
                               decisions=[handplay.Decision(situation, action, -0.5, theory, index)])

    def test_preflop(self):
        plays = [self.play("K9o", ["Kd", "9c"], "raise", {"raise": 0.3, "fold": 0.7}, ev=2.0),
                 self.play("T2o", ["Ts", "2d"], "raise", {"fold": 1.0}, index=4),
                 self.play("AA", ["Ah", "Ad"], "raise", {"raise": 1.0}),  # la théorie l'ouvre : pas un exemple
                 self.play("72o", ["7h", "2c"], "fold", {"fold": 1.0})]
        ex = leak_examples.preflop("sb_first.raise", "plus", plays, "Hero")  # ouvre trop large
        self.assertEqual((ex.total, [e.hand for e in ex.shown]), (2, ["H-T2o", "H-K9o"]))  # le plus loin d'abord
        first = ex.shown[0]
        self.assertEqual((first.cards, first.did, first.why, first.value, first.at),
                         (["Ts", "2d"], "open (T2o)", "théorie : fold 100 %", -0.5, 4))
        self.assertEqual(ex.shown[1].why, "théorie : fold 70 %, relance 30 %")
        tight = leak_examples.preflop("sb_first.raise", "moins", plays, "Hero")  # n'ouvre pas assez : aucune
        self.assertIsNone(tight)
        # tables à plusieurs : « pre:situation:position:mot »
        ring = [self.play("AQo", ["Ac", "Qd"], "call", {"raise": 0.8, "call": 0.2}, position="BB",
                          situation="vs_open")]
        ex = leak_examples.preflop("pre:vs_open:BB:3bet", "moins", ring, "Hero")
        self.assertEqual((ex.total, ex.shown[0].did, ex.shown[0].why),
                         (1, "call (AQo)", "théorie : relance 80 %, call 20 %"))

    def test_postflop_and_solver(self):
        hands = [h for h in load_hands([FIXTURES]) if len(h.seats) == 2]
        points = leakcheck.hu_points(hands, "Hero")
        ex = leak_examples.postflop("bet", "plus", points["srp:cbet"], "Hero")  # c-bet trop souvent
        cbet = ex.shown[0]
        hand = next(h for h in hands if h.hand_id == cbet.hand)
        self.assertEqual((hand.actions[cbet.at].player, hand.actions[cbet.at].street, len(cbet.board)),
                         ("Hero", "flop", 3))
        self.assertTrue(cbet.did.startswith("mise "))
        self.assertEqual(ex.value_label, "Rapporté à partir de là")
        self.assertIsNone(leak_examples.postflop("bet", "moins", points["srp:cbet"], "Hero"))  # jamais checké
        digests = [{"hand": "H9", "family": "srp", "decisions": [
            {"who": "H", "key": "bet:fi:", "d": 2, "ev_loss": 1.2, "frequency": 0.0, "combo": "AhKd",
             "board": ["2c", "7d", "9s"], "played": "mise 75 %", "strategy": [0.9, 0.1],
             "actions": [{"label": "check"}, {"label": "mise 75 %"}]},
            {"who": "H", "key": "bet:fi:", "d": 4, "ev_loss": 0.0, "frequency": 1.0, "combo": "AhKd", "board": [],
             "played": "check", "strategy": [1.0, 0.0], "actions": [{"label": "check"}, {"label": "mise"}]}]}]
        ex = leak_examples.solver("srp", "bet:fi:", digests)  # ses erreurs seulement
        self.assertEqual((ex.total, ex.shown[0].solver, ex.shown[0].value, ex.shown[0].cards, ex.shown[0].why),
                         (1, 2, -1.2, ["Ah", "Kd"], "solveur : check 90 %, mise 75 % 10 %"))

    def test_page(self):
        x = leaks.Leak("Préflop · Bouton : open", "90 % sur 40 occasions", "Resserre.", "préflop", "solide", 2.0,
                       key="sb_first.raise", direction="plus", freq=(0.9, 0.8, None, 40),
                       headline=leaks.headline("Préflop · Bouton : open", "aggr", "plus"))
        x.examples = leak_examples.preflop("sb_first.raise", "plus",
                                           [ExamplesTest.play("T2o", ["Ts", "2d"], "raise", {"fold": 1.0})], "Hero")
        y = leaks.Leak("SRP · BTN : C-bet flop", "80 % sur 30 occasions", "Checke plus.", "postflop", "indicatif",
                       1.0, key="srp:cbet", direction="plus", freq=(0.8, None, (0.5, 0.6), 30))
        report = SimpleNamespace(leaks=[y, x], table_format="HU")
        page = leaks_html(report, "/eleve/paul", False)
        self.assertEqual(x.summary, "Range trop large : open, au bouton")  # le leak en une phrase
        self.assertEqual(y.summary, y.title)  # sans phrase : son titre
        for text in ('<a class="nm" href="#lk-c-initiative">Après le flop, à l&#x27;initiative</a>',
                     '<li value="2"><a href="#lk-2" title="Préflop · Bouton : open">Range trop large : open, au bouton'
                     "</a></li>", '<div class="lk-h">Range trop large : open, au bouton</div>',
                     '<div class="lk-t">Préflop · Bouton : open <span', '<li value="2" id="lk-2">',
                     # en bref : le moment du coup de chacun, dans l'ordre d'importance
                     '<b>Au flop, à l&#x27;initiative</b> — <a href="#lk-1">SRP · BTN : C-bet flop</a>',
                     '<b>Préflop</b> — <a href="#lk-2">range trop large : open, au bouton</a>',
                     "1 leak, solide", "1 leak, indicatif", "<summary>Le détail : fréquence et mains (1)</summary>",
                     'href="/rejouer/H-T2o?api=/api/eleves/paul&amp;at=3"', "1 fois : des mains que la théorie joue",
                     '<span class="lb">Repère</span>', '<span class="v">50–60 %</span>'):
            self.assertIn(text, page)
        self.assertLess(page.index('id="lk-c-initiative"'), page.index('id="lk-c-preflop"'))
        alone = leaks_html(report, "/moi", True)
        self.assertNotIn("/rejouer/", alone)
        self.assertIn('<td class="small">H-T2o</td>', alone)  # le coup, sans lien


class HeadlineTest(unittest.TestCase):
    def test_headlines_and_themes(self):
        """Chaque leak en une phrase, en clair ; les leaks d'une catégorie regroupés par street."""
        self.assertEqual(leaks.headline("SRP · BTN : 2e barrel (turn)", "bet", "plus"),
                         "Tu mises trop souvent : 2e barrel (turn), au bouton, en SRP")
        self.assertEqual(leaks.headline("pot 3bet · BB : Fold face à la c-bet", "fold", "plus"),
                         "Tu te couches trop souvent face à la c-bet, en BB, en pot 3bet")
        self.assertEqual(leaks.headline("Préflop · premier à parler · CO : limp", "limp", "plus"),
                         "Trop de limps, au CO, premier à parler")
        self.assertEqual(leaks.headline("SRP, l'ouvreur en position · À l'initiative : C-bet flop", "bet", "moins"),
                         "Tu ne mises pas assez : c-bet flop, en SRP, l'ouvreur en position")
        group = {"label": "Face à : Relance turn du BTN"}
        dev = {"category": "passive", "observed": 0.8, "expected": 0.4, "z": 3.0}
        self.assertEqual(leaks.solver_headline(group, [dev], "pot 3bet"),
                         "Tu checkes ou paies trop souvent : face à relance turn du BTN, en pot 3bet")
        self.assertTrue(leaks.solver_headline(group, [], "SRP").startswith("Tu y perds de l'EV face au solveur"))
        # sans écart de fréquence net : ce qui coûte dans ses erreurs (il paie, le solveur se couche)
        call = {"who": "H", "key": "face:to:i:2", "d": 3, "chosen": 1, "ev_loss": 4.0, "frequency": 0.0,
                "evs": [0.0, -4.0, -6.0], "actions": [{"kind": "fold"}, {"kind": "call"}, {"kind": "raise"}]}
        digests = [{"hand": "H1", "family": "3bet", "decisions": [call, dict(call, ev_loss=0.1)]}]
        situation = dict(group, family="3bet", key="face:to:i:2")
        self.assertEqual(leaks.error_kind(digests, situation), ("passive", "fold"))
        self.assertEqual(leaks.solver_headline(situation, [], "pot 3bet", ("passive", "fold")),
                         "Tu paies là où le solveur se couche : face à relance turn du BTN, en pot 3bet")
        make = lambda title, key, source="postflop": leaks.Leak(title, "", "", source, "solide", 1.0, key=key)  # noqa
        found = [make("SRP · BTN : 2e barrel (turn)", "srp:barrel"),  # en pot 3bet, la BB 3bettrice hors de position
                 make("pot 3bet · C-bet", "solveur:3bet:bet:fo:", "solveur"),
                 make("pot 3bet · 2e barrel", "solveur:3bet:bet:to:o", "solveur"),
                 make("Préflop · Bouton : open", "sb_first.raise", "préflop")]
        self.assertEqual([leaks.street_of(x) for x in found], ["turn", "flop", "turn", "preflop"])
        themes = leaks.overview(found)
        self.assertEqual([(name, [k for k, _ in group]) for name, group in themes],
                         [("À la turn, à l'initiative", [1, 3]), ("Au flop, à l'initiative", [2]), ("Préflop", [4])])


class SolveOrderTest(unittest.TestCase):
    def test_hand_keys_and_orders(self):
        hands = {h.hand_id: h for h in load_hands([FIXTURES]) if len(h.seats) == 2}
        # HAND01 : c-bet du bouton (Hero), check-raise de la BB, fold
        self.assertEqual(leaks.hand_keys(hands["HAND01"], "Hero"), {"srp:cbet", "srp:fold_raise", "srp:reraise"})
        self.assertEqual(leaks.hand_keys(hands["HAND01"], "Villain"), {"srp:fold_flop", "srp:raise"})
        chosen = [hands["HAND01"], hands["HAND02"]]
        todo = review.PendingSpots(chosen, "Hero", ["SRP", "pot 3bet"])
        cbet = leaks.Leak("SRP · BTN : C-bet flop", "", "", "postflop", "solide", 1.0, key="srp:cbet",
                          focus=frozenset({"srp:cbet"}))
        preflop = leaks.Leak("Préflop · Bouton : open", "", "", "préflop", "solide", 2.0, key="sb_first.raise")
        orders = leaks.solve_orders(todo, "Hero", lambda h: leaks.hand_keys(h, "Hero"), [preflop, cbet], [])
        self.assertEqual(cbet.targets, [("HAND01", 3)])  # la seule main où se joue sa c-bet ; une résolution de SRP
        self.assertEqual((preflop.targets, orders["leaks"]), ([], [("HAND01", 3)]))
        losses = [h.net("Hero") / h.bb for h in chosen]
        expected = [h.hand_id for _, h in sorted(zip(losses, chosen), key=lambda t: t[0])]
        self.assertEqual([hand_id for hand_id, _ in orders["pertes"]], expected)  # les plus chères d'abord
        report = leaks.Report("Hero", 2, {}, {}, {}, [], {}, {}, [cbet, preflop], solve_orders=orders)
        self.assertEqual(leaks.solve_candidates(report, leak="srp:cbet"), [("HAND01", 3)])
        self.assertEqual(leaks.solve_candidates(report, "pertes"), orders["pertes"])


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
                     "Exporter : ", ">la synthèse PDF</a>", 'class="fold" data-fold="stats"', 'data-api="/api/leaks"',
                     'data-name="Villain"', 'data-query=""',
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

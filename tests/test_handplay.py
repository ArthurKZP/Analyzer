import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer import handplay, leaks
from analyzer.app.hands_page import build_hands_page
from analyzer.app.library import Library
from analyzer.parsers import load_hands
from analyzer.theory.preflop import load_solution

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

FIXTURES = Path(__file__).parent / "fixtures"
SITES = Path(__file__).parent / "sites"


def decisions(played):
    return [(d.situation, d.action, d.fold_bb) for d in played.decisions]


class ReadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.theory = handplay.Theory(load_solution())
        cls.by_id = {h.hand_id: h for h in load_hands([FIXTURES])}

    def read(self, hand_id):
        return handplay.read(self.by_id[hand_id], "Hero", self.theory)

    def test_heads_up(self):
        k9s = self.read("HAND01")
        self.assertEqual((k9s.combo, k9s.position, k9s.opponent, k9s.net_bb), ("K9s", "BTN", "Villain", -4.1))
        self.assertEqual(decisions(k9s), [("open", "raise", -0.5)])  # folder le bouton coûte la SB
        self.assertEqual(decisions(self.read("HAND02")), [("vs_open", "raise", -1.0)])  # la BB face à l'open
        aces = self.read("HAND03")
        self.assertEqual(decisions(aces), [("open", "raise", -0.5), ("vs_3bet", "allin", -2.5)])  # l'open perdu
        self.assertEqual((aces.net_bb, aces.ev_bb), (-100.0, 62.76))  # tapis payé : l'EV all-in ôte la chance
        self.assertAlmostEqual(sum(aces.decisions[1].theory.values()), 1.0, places=2)
        limp = self.read("HAND04")
        self.assertEqual(decisions(limp), [("vs_limp", "raise", None)])  # la BB peut checker : pas de fold
        self.assertIsNone(limp.decisions[0].theory)
        self.assertIsNone(handplay.read(self.by_id["HAND01"], "Personne"))

    def test_outcomes(self):
        Outcome = handplay.Outcome
        self.assertEqual(self.read("HAND01").outcome, Outcome("srp", "fold_f", "toppair", True))  # K72 : top pair
        self.assertEqual(self.read("HAND02").outcome, Outcome("3bet", "sd_win", "toppair", False))
        self.assertEqual(self.read("HAND03").outcome, Outcome("allin", "sd_lose"))  # tapis préflop
        self.assertEqual(self.read("HAND04").outcome, Outcome("won"))  # il folde à ta relance
        sites = {h.table_format + h.site: h for h in load_hands([SITES])}
        self.assertEqual(handplay.read(sites["6-maxUnibet"], "Hero").outcome, Outcome("multi", "sd_win", "pair"))
        self.assertEqual(handplay.read(sites["3-maxWinamax"], "Hero").outcome, Outcome("3bet", "sd_lose", "strong", False))
        self.assertEqual(handplay.read(sites["HUWinamax"], "Hero").outcome, Outcome("srp", "win_f", "air", True))
        self.assertEqual(handplay.read(sites["6-maxBetclic"], "Hero").outcome, Outcome("fold"))
        self.assertEqual(handplay._branches(self.read("HAND01").outcome),
                         ["p:srp", "e:srp:fold_f", "f:srp:toppair", "i:srp:ip"])

    def test_tables(self):
        hands = {h.table_format + h.site: h for h in load_hands([SITES])}
        kings = handplay.read(hands["3-maxWinamax"], "Hero")
        self.assertEqual((kings.combo, kings.position, kings.opponent), ("KK", "BB", None))
        self.assertEqual(decisions(kings), [("vs_open", "raise", -1.0)])
        iso = handplay.read(hands["6-maxUnibet"], "Hero")  # un limp puis une relance de la SB : face à l'open
        self.assertEqual((iso.combo, decisions(iso)), ("QTs", [("vs_open", "call", -1.0)]))
        folded = handplay.read(hands["6-maxBetclic"], "Hero")
        self.assertEqual((folded.combo, folded.position, folded.net_bb), ("J8o", "HJ", 0.0))
        self.assertEqual(decisions(folded), [("open", "fold", 0.0)])

    def test_chart_theory(self):
        lines = {"6-max": {
            "SB:raise BB:call": {"SB": {"QTs": 0.5, "AKs": 1.0}, "BB": {"QTs": 0.4}},
            "SB:raise BB:raise SB:call": {"SB": {"QTs": 0.25}, "BB": {"QTs": 0.2, "AKs": 1.0}},
            "SB:raise BB:raise SB:raise BB:call": {"SB": {"QTs": 0.1}, "BB": {}},
        }}
        theory = handplay.Theory(None, lines)
        hand = next(h for h in load_hands([SITES]) if h.site == "Unibet")
        iso = handplay.read(hand, "Hero", theory)
        self.assertEqual(iso.decisions[0].theory, {"raise": 0.2, "call": 0.4, "fold": 0.4})
        self.assertEqual(handplay._chart_open(lines["6-max"], "SB", "QTs"), {"raise": 0.5, "fold": 0.5})
        # face au 3bet : la ligne garde ce qui ouvre ET paie, rapporté à la range d'ouverture
        vs_3bet = handplay._chart_vs_3bet(lines["6-max"], "SB", "BB", "QTs")
        self.assertEqual({k: round(v, 6) for k, v in vs_3bet.items()}, {"raise": 0.2, "call": 0.5, "fold": 0.3})
        self.assertIsNone(handplay._chart_vs_3bet(lines["6-max"], "SB", "BB", "72o"))  # jamais ouverte
        self.assertIsNone(handplay.Theory(None, {}).strategy(hand, "Hero", "open", [], "QTs"))


def play(combo, position, ev, situation="vs_open", action="call", fold=-1.0, theory=None, opponent="A",
         outcome=None, loss=None):
    p = handplay.Played(None, combo, position, ev, ev, opponent, [handplay.Decision(situation, action, fold, theory)],
                        outcome)
    p.solver_loss = loss
    return p


class AggregateTest(unittest.TestCase):
    def test_cells(self):
        plays = [play("AKs", "BB", 4.0, theory={"raise": 0.7, "call": 0.3}),
                 play("AKs", "BB", -2.0, action="allin", theory={"raise": 0.7, "allin": 0.2}, opponent="B"),
                 play("72o", "BTN", -0.5, "open", "fold", -0.5, {"raise": 0.0, "fold": 1.0})]
        data = handplay.aggregate(plays, {"A": "reg", "B": "rec"})
        cells = data["cells"]
        self.assertEqual((data["hands"], data["positions"]), (3, ["BTN", "BB"]))
        self.assertEqual(cells["BB|all|all|reg"]["AKs"], [1, 4.0, 16.0, 4.0, 16.0, 0.0, 0.0, 0])
        self.assertEqual(cells["BB|vs_open|call|reg"]["AKs"], [1, 4.0, 16.0, 4.0, 16.0, -1.0, 0.3, 1])
        self.assertEqual(cells["BB|vs_open|allin|rec"]["AKs"][6:], [0.9, 1])  # le tapis compte avec les relances
        self.assertEqual(cells["BTN|open|fold|reg"]["72o"][5:], [-0.5, 1.0, 1])

    def test_detail(self):
        Outcome = handplay.Outcome
        srp, three = Outcome("srp", "sd_lose", "air", True), Outcome("3bet", "fold_t", "pair", True)
        plays = [play("K9s", "BTN", -6.0, "open", "raise", -0.5, {"raise": 1.0}, outcome=srp, loss=2.0),
                 play("K9s", "BTN", 1.0, "open", "raise", -0.5, {"raise": 1.0}, outcome=Outcome("won")),
                 play("K8s", "BTN", -12.0, "open", "raise", -0.5, {"raise": 0.5, "fold": 0.5}, outcome=three),
                 play("K9s", "BTN", -0.5, "open", "fold", -0.5, {"raise": 1.0}),
                 play("K9s", "BTN", -3.0, "open", "raise", -0.5, outcome=srp, opponent="B")]
        plays[2].decisions.append(handplay.Decision("vs_3bet", "call", -2.5, {"call": 0.4, "fold": 0.6}))
        kinds = {"A": "reg", "B": "rec"}
        d = handplay.detail(plays, kinds, name="K9s", kind="reg", position="BTN", situation="open")
        self.assertEqual(d["n"], 2)  # le fold et l'adversaire récréatif ne comptent pas
        self.assertEqual(d["branches"]["p:srp"], [1, -6.0, 36.0, -6.0, 36.0, -0.5, 2.0, 1])
        self.assertEqual(d["branches"]["p:won"][0], 1)
        self.assertEqual(d["mix"]["here"], [3, 3, 2, 0, 1, 3.0, 0.0, 0.0])  # 2 relances, 1 fold ; théorie : relance
        self.assertIsNone(d["mix"]["next"])
        self.assertEqual([r[1:2] + r[5:9] for r in d["review"]], [["", "srp", "sd_lose", "air", 2.0]])  # pas « won »
        family = handplay.detail(plays, kinds, name="Autres assorties", situation="open", actions=("raise", "allin"))
        self.assertEqual(family["n"], 4)  # K9s et K8s, tous types d'adversaires
        self.assertEqual(family["mix"]["next"], [1, 1, 0, 1, 0, 0.0, 0.4, 0.6])  # face au 3bet ensuite
        self.assertEqual([r[0] for r in family["review"]], ["K8s", "K9s", "K9s"])  # du plus cher au moins cher
        everything = handplay.detail(plays, kinds, name="K9s")
        self.assertEqual((everything["n"], everything["mix"]["here"]), (4, None))  # toutes les mains, une fois chacune


class LosersTest(unittest.TestCase):
    def test_families_and_hands(self):
        noise = (0.5, -0.5)
        plays = [play("Q7o", "BB", -3 + noise[k % 2], theory={"call": 0.05}) for k in range(20)]
        plays += [play("K9s", "BB", -3 + noise[k % 2], theory={"call": 0.6}) for k in range(20)]
        plays += [play("Q8s", "BB", 5 + noise[k % 2]) for k in range(60)]  # la famille de K9s gagne
        plays += [play("AKs", "BB", 5 + noise[k % 2]) for k in range(20)]
        plays += [play("J2o", "BB", -1, action="fold") for k in range(30)]  # les folds ne comptent pas
        found = {x.name: x for x in handplay.losers(plays)}
        self.assertEqual(set(found), {"Autres dépareillées", "K9s"})  # Q7o : déjà dans sa famille
        k9s = found["K9s"]
        self.assertEqual((k9s.n, k9s.mean, k9s.fold, k9s.gap), (20, -3.0, -1.0, -2.0))
        self.assertAlmostEqual(k9s.theory, 0.6)
        self.assertEqual(k9s.label, "K9s : call face à l'open (BB)")
        self.assertEqual(handplay.losers(plays[:10]), [])  # trop peu de mains
        self.assertEqual([handplay.family(c) for c in ("TT", "77", "22", "A5s", "KQo", "87s", "97s", "96s", "J4o")],
                         ["Paires hautes (TT+)", "Paires moyennes (66-99)", "Petites paires (22-55)", "As assortis",
                          "Broadways dépareillés", "Connecteurs assortis", "Connecteurs assortis",
                          "Autres assorties", "Autres dépareillées"])

    def test_sources(self):
        Outcome = handplay.Outcome
        plays = [play("K9s", "BTN", -10.0 + k % 2, "open", "raise", -0.5, outcome=Outcome("3bet", "sd_lose", "pair", True),
                      loss=1.5 if k < 2 else None) for k in range(10)]
        plays += [play("K9s", "BTN", 1.0, "open", "raise", -0.5, outcome=Outcome("won")) for _ in range(10)]
        x = handplay.losers(plays)[0]
        self.assertEqual([(s.pot, s.n) for s in x.sources], [("3bet", 10), ("won", 10)])  # la pire suite d'abord
        three = x.sources[0]
        self.assertAlmostEqual(three.mean, -9.5)
        self.assertAlmostEqual(three.contribution, 10 * (-9.5 + 0.5) / 20)  # les parts s'additionnent en l'écart
        self.assertAlmostEqual(sum(s.contribution for s in x.sources), x.gap)
        self.assertEqual((three.solver_n, three.solver_loss), (2, 1.5))
        self.assertEqual(len(x.worst), 3)
        self.assertTrue(all(ev == -10.0 for _, ev, _ in x.worst))  # les coups allés au flop les plus chers

    def test_leak(self):
        x = handplay.Loser("Q7o", "vs_open", "BB", "call", 20, -3.0, -1.0, 0.5, 0.05,
                           [handplay.Source("srp", 20, -3.0, -2.0, 2, 1.25)], [("H7", -12.0, 3.0)])
        leak = leaks._hand_leak(x, 400)
        self.assertEqual(leak.title, "Préflop · Q7o : call face à l'open (BB)")
        self.assertIn("20 fois contre les réguliers", leak.evidence)
        self.assertIn("-200 bb/100 par rapport au fold", leak.evidence)
        self.assertIn("La perte vient surtout du SRP (20 fois, -3,0 bb par main : -200 bb/100).", leak.evidence)
        self.assertIn("Le solveur y voit 1,25 bb perdus par coup après le flop (2 coups analysés)", leak.evidence)
        self.assertIn("folde-la ici", leak.advice)
        self.assertEqual(leak.link, "mains#fmt=HU&kind=reg&pos=BB&sit=vs_open&main=Q7o&act=pas")
        self.assertEqual(leak.example, ("H7", 0, 12.0))
        played = handplay.Loser("K9s", "vs_open", "BB", "call", 20, -3.0, -1.0, 0.5, 0.6,
                                [handplay.Source("3bet", 5, -9.0, -2.0)])
        self.assertIn("la perte vient de la suite du coup, dans les pots 3bet", leaks._hand_leak(played, 400).advice)
        folds = handplay.Loser("K9s", "open", "BTN", "raise", 20, -3.0, -0.5, 0.5, 0.9,
                               [handplay.Source("fold", 8, -2.5, -0.8)])
        self.assertIn("l'abandonnes trop souvent", leaks._hand_leak(folds, 400).advice)


class PageTest(unittest.TestCase):
    def test_embedded_data(self):
        data = {"HU": handplay.aggregate([play("AKs", "BB", 4.0, opponent="</script>")])}
        page = build_hands_page(data)
        self.assertNotIn("<h1>", page)
        payload = re.search(r'<script type="application/json" id="hp-data">(.*?)</script>', page, re.S).group(1)
        self.assertEqual(json.loads(payload)["formats"]["HU"]["hands"], 1)
        self.assertIn("−100 bb/100", page)
        self.assertIn("<h1>", build_hands_page(data, embed=False))
        self.assertIn("Aucune main", build_hands_page({}))
        self.assertIn('"api":"/api/mains"', build_hands_page(data, api="/api/mains"))

    def test_library(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": tmp}):
            folder = Path(tmp) / "mains"
            folder.mkdir()
            for path in list(FIXTURES.glob("*.txt")) + list(SITES.glob("*.txt")):
                shutil.copy(path, folder / path.name)
            lib = Library(folder)
            page = lib.self_page("mains")
            payload = json.loads(re.search(r'id="hp-data">(.*?)</script>', page, re.S).group(1))
            self.assertEqual(list(payload["formats"]), ["HU", "ring"])  # 3-max et 6-max ensemble
            self.assertEqual(payload["formats"]["ring"]["hands"], 3)
            self.assertEqual(payload["api"], "/api/mains")
            detail = lib.hands_detail({"main": "K9s", "pos": "BTN", "sit": "open", "act": "agg"})
            self.assertEqual((detail["n"], list(detail["branches"])[0]), (1, "p:srp"))
            ring = lib.hands_detail({"fmt": "ring", "main": "KK"})
            self.assertEqual(ring["review"][0][5], "3bet")
            with self.assertRaises(KeyError):
                lib.hands_detail({"main": "K9s", "sit": "nulle"})
            with self.assertRaises(KeyError):
                lib.hands_detail({"fmt": "6-max", "main": "K9s"})


if __name__ == "__main__":
    unittest.main()

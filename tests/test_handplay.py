import json
import re
import unittest
from pathlib import Path

from analyzer import handplay, leaks
from analyzer.app.hands_page import build_hands_page
from analyzer.parsers import load_hands
from analyzer.theory.preflop import load_solution

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


def play(combo, position, ev, situation="vs_open", action="call", fold=-1.0, theory=None, opponent="A"):
    return handplay.Played(None, combo, position, ev, ev, opponent,
                           [handplay.Decision(situation, action, fold, theory)])


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

    def test_leak(self):
        x = handplay.Loser("Q7o", "vs_open", "BB", "call", 20, -3.0, -1.0, 0.5, 0.05)
        leak = leaks._hand_leak(x, 400)
        self.assertEqual(leak.title, "Préflop · Q7o : call face à l'open (BB)")
        self.assertIn("20 fois contre les réguliers", leak.evidence)
        self.assertIn("-200 bb/100 par rapport au fold", leak.evidence)
        self.assertIn("folde-la ici", leak.advice)
        self.assertEqual(leak.link, "mains")
        self.assertIn("la suite du coup", leaks._hand_leak(
            handplay.Loser("K9s", "vs_open", "BB", "call", 20, -3.0, -1.0, 0.5, 0.6), 400).advice)


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


if __name__ == "__main__":
    unittest.main()

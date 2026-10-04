import json
import os
import tempfile
import unittest
from unittest import mock

from analyzer.theory import coach, postflop
from analyzer.theory.studyspots import StudySpot, cards_of

BOARD = ["Ks", "7d", "2c"]
COMBOS = ["AhAd", "KhQh", "8h8c", "QhJh", "6h5h", "AcTd", "4c3c"]
TURNS = ["Ah", "2h", "9c", "3h"]
RIVERS = ["3d", "Kc"]


def make_query(board=BOARD):
    """Un petit arbre aux règles du SRP : la BB (0) ne mène pas dans celui qui a misé à la street précédente,
    check-check ou mise payée ouvrent la street suivante. Les mains qui commencent par un As misent ou
    relancent, les autres checkent ou paient : de quoi vérifier ce que l'extraction rassemble."""
    def options(kinds, prev_aggressor):
        player = len(kinds) % 2
        bets = sum(k in ("bet", "raise") for k in kinds)
        if not kinds:
            return 0, (["check"] if prev_aggressor == 1 else ["check", "bet"])
        if bets == 0:
            return player, ["check", "bet"]
        return player, (["fold", "call", "raise"] if bets == 1 else ["fold", "call"])

    def query(path):
        cards, kinds, prev, aggressor = list(board), [], 1, None
        for step in path:
            if step["type"] == "card":
                cards.append(step["card"])
                kinds, prev, aggressor = [], aggressor, None
                continue
            player, opts = options(kinds, prev)
            kinds = kinds + [opts[step["index"]]]
            if kinds[-1] in ("bet", "raise"):
                aggressor = player
        pot = 5.0
        empty = {"board": cards, "pot": pot, "actions": [], "hands": [[], []], "player": None, "cards": None}
        if kinds and kinds[-1] == "fold":
            return dict(empty, type="terminal_fold")
        if kinds[-2:] == ["check", "check"] or (kinds and kinds[-1] == "call"):
            if len(cards) == 5:
                return dict(empty, type="terminal_showdown")
            deck = TURNS if len(cards) == 3 else RIVERS
            return dict(empty, type="chance", cards=[c for c in deck if c not in cards])
        player, opts = options(kinds, prev)
        actions = [{"kind": k, "amount": {"check": 0.0, "fold": 0.0, "bet": 1.65, "call": 1.65}.get(k, 5.0),
                    "allin": False} for k in opts]
        hands = [[], []]
        for p in (0, 1):
            for combo in COMBOS:
                if set(cards_of(combo)) & set(cards):
                    continue
                row = [combo, 1.0, 0.6 if p == 1 else 0.4, 0.0]
                if p == player:
                    target = len(opts) - 1 if combo.startswith("A") else (opts.index("call") if "call" in opts else 0)
                    row += [1.0 if i == target else 0.0 for i in range(len(opts))]
                    row += [2.0 if i == target else 1.0 for i in range(len(opts))]  # EV de chaque action
                hands[p].append(row)
        return {"type": "action", "board": cards, "pot": pot, "player": player, "actions": actions,
                "hands": hands, "cards": None}
    return query


class BucketTest(unittest.TestCase):
    def test_buckets_and_cards(self):
        cases = {"KhKc": "nuts", "AhAd": "overpair", "KhQh": "tp_good", "Kh5h": "tp_weak", "8h8c": "midpair",
                 "7h6h": "midpair", "As3d": "high", "As3s": "weakdraw", "9h8c": "air", "QhJh": "air"}
        for combo, expected in cases.items():  # sur K♠7♦2♣ ; A♠3♠ : backdoor couleur avec le K♠
            self.assertEqual(coach.bucket(combo, BOARD), expected, combo)
        self.assertEqual(coach.bucket("AhQh", ["Kh", "7h", "2c"]), "fd")
        self.assertEqual(coach.bucket("9h8h", ["Th", "7c", "2d"]), "sd")
        self.assertEqual(coach.bucket("AhQh", ["Kh", "7h", "2c", "3d", "4s"]), "high")  # pas de tirage à la river
        for card, expected in {"As": "over", "Kc": "paired", "9s": "brick"}.items():
            self.assertEqual(coach.card_class(BOARD, card), expected, card)
        for card, expected in {"6d": "straight", "Ts": "over", "4h": "brick", "9c": "paired"}.items():
            self.assertEqual(coach.card_class(["9s", "8d", "2c"], card), expected, card)
        self.assertEqual(coach.card_class(["Ks", "7s", "2c"], "3s"), "flush")
        self.assertEqual(coach.card_class(["Ks", "Qd", "Jc"], "Ah"), "over")  # l'overcard passe avant la quinte

    def test_action_classes(self):
        node = {"pot": 10.0}
        for action, expected in (({"kind": "bet", "amount": 3.3}, "small"), ({"kind": "bet", "amount": 7.5}, "medium"),
                                 ({"kind": "bet", "amount": 11.0}, "big"), ({"kind": "bet", "amount": 15.0}, "overbet"),
                                 ({"kind": "bet", "amount": 90.0, "allin": True}, "allin"),
                                 ({"kind": "raise", "amount": 20.0}, "raise"), ({"kind": "call", "amount": 3.0}, "call")):
            self.assertEqual(coach.action_class(node, action), expected, action)


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ANALYZER_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_extract_lines(self):
        spot = StudySpot("srp", list(BOARD), plan={})
        with mock.patch.object(coach, "RIVER_TURNS", 49):  # toutes les turns jusqu'à la river
            data = coach.extract(make_query(), spot, "cle")
        nodes = data["nodes"]
        self.assertEqual(set(nodes), {"cbet", "vs_cbet", "vs_xr", "barrel", "vs_barrel", "delayed", "probe",
                                      "barrel3", "vs_barrel3"})
        cbet = nodes["cbet"]["groups"]["flop"]
        self.assertEqual((nodes["cbet"]["actor"], cbet["n"]), (1, 1))
        self.assertAlmostEqual(sum(cbet["f"].values()), 1.0, places=3)
        self.assertEqual(cbet["b"]["overpair"]["f"], {"small": 1.0})  # AA mise (2,5 dans 5 : petite mise)
        self.assertEqual(cbet["b"]["tp_good"]["f"], {"check": 1.0})
        # une turn par carte, rangée selon son effet sur le board
        barrel = nodes["barrel"]["groups"]
        self.assertEqual({k: g["n"] for k, g in barrel.items()}, {"over": 1, "paired": 1, "brick": 2})
        self.assertEqual(sum(g["n"] for g in nodes["barrel3"]["groups"].values()), len(TURNS) * len(RIVERS))
        self.assertEqual(nodes["vs_cbet"]["groups"]["flop"]["b"]["overpair"]["f"], {"raise": 1.0})
        self.assertEqual(data["advantages"], {"eq": [0.4, 0.6], "nuts": [0.0, 0.0]})

        coach.save_plan(data)
        self.assertEqual(coach.load_plan("cle")["id"], "spot:srp:Ks7d2c")
        coach.plan_path("cle").write_text(json.dumps(dict(data, version=0)), encoding="utf-8")
        self.assertIsNone(coach.load_plan("cle"))  # extraction d'une autre version : à refaire


class SynthesisTest(unittest.TestCase):
    def test_rules_and_patterns(self):
        self.assertEqual([coach.pattern_of(x) for x in (0.9, 0.6, 0.4, 0.1)], ["range", "frequent", "mixed", "check"])
        merged = coach.merge([
            {"n": 1, "f": {"small": 0.8, "check": 0.2},
             "b": {"nuts": {"w": 0.2, "f": {"small": 1.0}}, "midpair": {"w": 0.5, "f": {"check": 0.6, "small": 0.4}},
                   "air": {"w": 0.3, "f": {"small": 0.9, "check": 0.1}}}},
            {"n": 1, "f": {"small": 0.6, "check": 0.4},
             "b": {"nuts": {"w": 0.2, "f": {"small": 1.0}}, "midpair": {"w": 0.5, "f": {"check": 1.0}},
                   "air": {"w": 0.3, "f": {"small": 0.5, "check": 0.5}}}},
        ])
        self.assertAlmostEqual(merged["f"]["small"], 0.7)
        self.assertAlmostEqual(merged["b"]["midpair"]["f"]["check"], 0.8)
        self.assertEqual(coach.bettor_rules(merged), [("Mise", "Deux paires et mieux · Rien"), ("Check", "Paire moyenne")])
        river = coach.bettor_rules(merged, river=True)
        self.assertEqual(river[:2], [("Mise pour la valeur", "Deux paires et mieux"), ("Bluffe", "Rien")])
        facing = {"n": 1, "f": {}, "b": {"nuts": {"w": 0.3, "f": {"raise": 0.8, "call": 0.2}},
                                         "tp_good": {"w": 0.3, "f": {"call": 0.9, "fold": 0.1}},
                                         "air": {"w": 0.4, "f": {"fold": 0.95, "call": 0.05}}}}
        self.assertEqual(coach.defender_rules(facing),
                         [("Relance", "Deux paires et mieux"), ("Paie", "Top pair, bon kicker"), ("Folde", "Rien")])
        text = coach.why("mixed", 0.16, -0.06, "le bouton", "la BB")
        self.assertIn("La BB a plus de mains très fortes", text)
        self.assertIn("+16 pts", coach.why("range", 0.16, 0.02, "le bouton", "la BB"))


class PlanPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ANALYZER_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_family_plan_and_page(self):
        from analyzer.app.plan_page import build_coach_page
        spot = StudySpot("srp", list(BOARD), plan={})
        key = postflop.study_key(spot.request())
        data = coach.extract(make_query(), spot, key)
        coach.save_plan(data)
        studies = {spot.ident: {"id": spot.ident, "key": key, "family": "srp", "board": BOARD}}
        with mock.patch.object(coach.studyspots, "spot_studies", lambda: studies):
            plan = coach.family_plan("srp")
            self.assertEqual((plan["count"], plan["missing"], plan["who"]), (1, 0, "le bouton"))
            group = plan["groups"][0]
            self.assertEqual(group["pattern"], "check")  # 2 mains sur 7 misent
            self.assertTrue(group["turn"] and group["river"] and group["defense"]["vs_cbet"])
            page = build_coach_page({"missing": 0, "busy": 0})
            for text in ("Plan de jeu suggéré", "Check fréquent", "Pourquoi ?", "2e barrel", "En face : la BB"):
                self.assertIn(text, page)
            self.assertEqual(coach.missing(), [])


if __name__ == "__main__":
    unittest.main()

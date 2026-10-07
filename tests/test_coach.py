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
        self.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": self.tmp.name})
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
        coach.save_plan(dict(data, version=0))
        self.assertIsNone(coach.load_plan("cle"))  # extraction d'une autre version : à refaire


class SynthesisTest(unittest.TestCase):
    def test_rules_and_patterns(self):
        self.assertEqual([coach.strategy_of(x) for x in (0.9, 0.6, 0.4, 0.1, 0.698, 0.354)],
                         ["range", "mixte", "mixte", "check", "range", "check"])  # lue comme elle s'affiche
        self.assertEqual([coach.size_group(c) for c in ("small", "medium", "overbet", "allin", None)],
                         ["petite", "moyenne", "grosse", "tapis", "petite"])
        self.assertAlmostEqual(coach.max_fold([33]), 0.33 / 1.33)
        self.assertIsNone(coach.max_fold([]))
        self.assertIn("défense large", coach.defense_text(0.15, 0.1, 0.25, "la BB", "petite mise"))
        self.assertIn("défense serrée", coach.defense_text(0.40, 0.1, 0.25, "la BB", "petite mise"))
        self.assertEqual(coach.defense_text(0.5, 0.0, None, "la BB", "tapis"), "La BB folde 50 % et relance 0 %.")
        cases = {"KsKd4c": "paire", "As8s3s": "monotone", "Ks8d3h": "haut-sec", "Kd7d5c": "haut-couleur",
                 "KhQc9d": "haut-connecte", "Qs7d2h": "moyen-sec", "JhTc8d": "moyen-connecte", "9s5d2h": "bas-sec",
                 "6c4c2d": "bas-connecte", "Ah4c2d": "haut-connecte"}  # A42 : roue possible
        for board, expected in cases.items():
            self.assertEqual(coach.board_category([board[i:i + 2] for i in (0, 2, 4)]), expected, board)
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
        simple = coach.simple_rules(merged)
        self.assertEqual([(r["label"], r["kind"], round(r["pct"], 2)) for r in simple],
                         [("Fortes", "bet", 1.0), ("Moyennes", "check", 0.2), ("Rien", "bet", 0.7)])
        self.assertEqual(coach.simple_rules(merged, river=True)[2]["verdict"], "Bluffe")
        river = coach.bettor_rules(merged, river=True)
        self.assertEqual(river[:2], [("Mise pour la valeur", "Deux paires et mieux"), ("Bluffe", "Rien")])
        facing = {"n": 1, "f": {}, "b": {"nuts": {"w": 0.3, "f": {"raise": 0.8, "call": 0.2}},
                                         "tp_good": {"w": 0.3, "f": {"call": 0.9, "fold": 0.1}},
                                         "air": {"w": 0.4, "f": {"fold": 0.95, "call": 0.05}}}}
        self.assertEqual(coach.defender_rules(facing),
                         [("Relance", "Deux paires et mieux"), ("Paie", "Top pair, bon kicker"), ("Folde", "Rien")])
        self.assertEqual([(r["label"], r["verdict"]) for r in coach.simple_rules(facing, defender=True)],
                         [("Fortes", "Paie"), ("Rien", "Folde")])  # relance 40 %, paie 55 %
        text = coach.why("mixte", 0.16, -0.06, "le bouton", "la BB")
        self.assertIn("La BB a plus de mains très fortes", text)
        self.assertIn("+16 pts", coach.why("range", 0.16, 0.02, "le bouton", "la BB"))


class PlanPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": self.tmp.name})
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
        with mock.patch.object(coach.studyspots, "spot_studies", lambda families=None: studies):
            plan = coach.family_plan("srp")
            self.assertEqual((plan["count"], plan["missing"], plan["who"], plan["other"]), (1, 0, "le bouton", "la BB"))
            self.assertEqual([(st["key"], st["flops"], st["groups"]) for st in plan["strategies"]],
                             [("range", 0, []), ("mixte", 0, []), ("check", 1, ["check-petite"])])  # 2 mains sur 7 misent
            group = plan["groups"][0]
            self.assertEqual((group["size_text"], group["categories"]), ("petite mise (33 % du pot)", [("Haut · sec", 1)]))
            attack, defense = group["attack"], group["defense"]
            self.assertTrue(attack["turn"] and attack["river"] and attack["delayed"] and attack["vs_xr"])
            self.assertEqual(attack["turn"][0]["text"], "plus haute que les cartes du flop")
            self.assertTrue(defense["vs_cbet"] and defense["vs_barrel"] and defense["probe"])  # la BB mène à la turn
            self.assertIsNone(defense["stab"])  # (le stab : quand l'attaquant est hors de position)
            self.assertAlmostEqual(defense["max_fold"], 0.33 / 1.33)
            self.assertIn("La BB folde", defense["text"])
            self.assertEqual(plan["flops"][0]["fold"], defense["vs_cbet"]["fold"])
            page = build_coach_page({"missing": 0, "busy": 0})
            for text in ("Plan de jeu suggéré", "Au flop : trois stratégies de c-bet", "Miser range", "Stratégie mixte",
                         "Checker range", "Petite mise (33 % du pot)", 'href="#att-srp-check-petite"',
                         'href="#def-srp-check-petite"', "En attaque · le bouton", "En défense · la BB",
                         "Face au 2e barrel, selon la turn", "la BB mène à la turn (probe)",
                         "Aucun flop résolu ne s'y range : sur ces flops, le bouton mise au plus",
                         "/explorateur/spot%3Asrp%3AKs7d2c", "À la turn, si la c-bet est payée",
                         "selon la stratégie du bouton", "la défense de la BB"):
                self.assertIn(text, page)
            self.assertNotIn(">Cartes<", page)
            self.assertEqual(coach.missing(), [])


class RingPlanTest(unittest.TestCase):
    """Le plan de jeu des tables à plusieurs : mêmes lignes que le heads-up de même structure, vraies positions."""
    LINES = {"CO:raise BB:raise CO:raise BB:call": {"CO": "AA,KK,AKs,AKo,KQs,QJs", "BB": "88,65s,43s,ATo"},
             "BTN:raise BB:call": {"BTN": "AA,KK,KQs", "BB": "88,QJs"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        from analyzer import db
        db.close_all()
        self.env.stop()
        self.tmp.cleanup()

    def charts(self):
        from analyzer.theory import ring_ranges
        ring_ranges.save_solution("6-max", {"lines": {k: {"ranges": v} for k, v in self.LINES.items()}})

    def test_lines_and_seats(self):
        self.assertEqual(coach.seat_words("srp"), ("la BB", "le bouton"))
        self.assertEqual(coach.seat_words("6max_bb_co_4bet"), ("la BB", "le CO"))
        self.assertEqual(coach.seat_names("6max_sb_bb_srp"), ("SB", "BB"))
        titles = dict((k, t) for k, t, _ in coach.lines("6max_sb_bb_srp"))  # la SB ouvre : initiative hors de position
        self.assertEqual((titles["cbet"], titles["vs_cbet"], titles["stab"]),
                         ("C-bet de la SB", "BB face à la c-bet", "Stab de la BB (la SB checke)"))
        titles = dict((k, t) for k, t, _ in coach.lines("6max_bb_co_4bet"))
        self.assertEqual((titles["cbet"], titles["vs_xr"]), ("C-bet du CO", "CO face au check-raise"))
        self.assertEqual([s for _, _, s in coach.lines("6max_bb_co_4bet")], [s for _, _, s in coach.LINES_IP])
        self.assertEqual(coach.aggressor_of("6max_sb_bb_srp"), 0)
        self.assertIn("6max_bb_btn_srp", coach.plan_families())

    def test_ring_plan_and_page(self):
        from analyzer.app.plan_page import build_coach_page
        page = build_coach_page({"missing": 0, "busy": 0})
        self.assertIn("charge d'abord tes charts 6-max", page)  # sans charts : pas d'onglets 6-max
        self.assertNotIn('data-fam="6max_bb_co_4bet"', page)
        self.charts()
        spot = StudySpot("6max_bb_co_4bet", list(BOARD), plan={})
        key = postflop.study_key(spot.request())
        data = coach.extract(make_query(), spot, key)  # le CO (1) à l'initiative, la BB ne mène pas
        self.assertEqual((data["family"], data["nodes"]["cbet"]["title"]), ("6max_bb_co_4bet", "C-bet du CO"))
        studies = {spot.ident: {"id": spot.ident, "key": key, "family": "6max_bb_co_4bet", "board": BOARD}}
        with mock.patch.object(coach.studyspots, "spot_studies", lambda families=None: studies):
            self.assertEqual(coach.missing(), [spot.ident])  # les études 6-max attendent aussi leur lecture
            self.assertEqual(coach.missing("srp"), [])
            coach.save_plan(data)
            plan = coach.family_plan("6max_bb_co_4bet")
            self.assertEqual((plan["count"], plan["who"], plan["other"], plan["ring"]), (1, "le CO", "la BB", True))
            self.assertEqual(plan["title"], "6-max, BB contre CO, pot 4bet")
            self.assertIn("Le CO a", plan["groups"][0]["why"])
            self.assertEqual(coach.family_plan("srp")["count"], 0)  # chaque famille ses flops
            page = build_coach_page({"missing": 0, "busy": 0})
        for text in ('data-fam="6max_bb_co_4bet"', "BB contre CO", "Au flop, le CO mise", "En défense · la BB",
                     'href="#att-6max_bb_co_4bet-check-petite"', "Résume-moi le plan de jeu 6-max, BB contre CO, pot 4bet",
                     "Le CO face au check-raise", "Face au check-raise"):
            self.assertIn(text, page)
        self.assertIn("Pas de ranges 6-max pour « SB open, BB call »", page)  # ligne absente de tes charts
        self.assertIn("Pas encore de plan pour : 6-max, BB contre BTN, SRP", page)

    def test_ring_study_gives_its_plan(self):
        self.charts()
        spot = StudySpot("6max_bb_btn_srp", list(BOARD), plan={})
        with mock.patch.object(coach, "extract_and_save") as extract:
            spot.after_solve(None)
        extract.assert_called_once()
        self.assertEqual(coach.node_brief({"type": "action", "board": BOARD, "pot": 5.0, "player": 1,
                                           "actions": [{"kind": "check", "amount": 0.0}],
                                           "hands": [[["8h8c", 1.0, 0.4, 0.0, 1.0]], [["AhAd", 1.0, 0.6, 0.0, 1.0]]]},
                                          names=("BB", "CO"))["joueur"], "CO")


if __name__ == "__main__":
    unittest.main()

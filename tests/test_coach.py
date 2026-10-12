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
                                      "vs_probe", "barrel3", "vs_barrel3", "river_xx", "vs_river_xx"})
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
        # ce que vaut chaque action pour chaque famille de mains (en part du pot, range de poids 1)
        ev = nodes["cbet"]["evs"]["flop"]
        self.assertEqual((ev["n"], ev["classes"], ev["sizes"]), (1, ["check", "small"], {"small": 33}))
        self.assertAlmostEqual(sum(g["r"] for g in ev["b"].values()), 1.0, places=3)
        # une case : boîte d'équité (AA à 60 % : de 50 à 75 %) et famille de mains
        aa = ev["b"]["e3|overpair"]
        self.assertEqual(aa["e"], {"check": round(1 / 7 / 5, 5), "small": round(2 / 7 / 5, 5)})
        self.assertEqual(aa["s"], aa["e"]["small"])  # AA mise : la stratégie
        self.assertEqual(aa["f"], {"small": round(1 / 7, 4)})  # (une part nulle n'est pas gardée)
        self.assertEqual(nodes["vs_cbet"]["evs"]["flop"]["classes"], ["fold", "call", "raise"])
        turn = nodes["barrel"]["evs"]  # à la turn : par type de carte, la moyenne de ses cartes
        self.assertEqual({k: g["n"] for k, g in turn.items()}, {"over": 1, "paired": 1, "brick": 2})
        self.assertAlmostEqual(sum(g["r"] for g in turn["brick"]["b"].values()), 1.0, places=3)
        self.assertEqual(sum(g["n"] for g in nodes["barrel3"]["evs"].values()), len(TURNS) * len(RIVERS))
        # la river après une turn checkée (le bouton, puis la BB face à sa mise)
        self.assertEqual(sum(g["n"] for g in nodes["river_xx"]["evs"].values()), len(TURNS) * len(RIVERS))
        self.assertEqual((nodes["river_xx"]["actor"], nodes["vs_river_xx"]["actor"]), (1, 0))
        self.assertEqual(data["version"], coach.VERSION)

        coach.save_plan(data)
        self.assertEqual(coach.load_plan("cle")["id"], "spot:srp:Ks7d2c")
        coach.save_plan(dict(data, version=0))
        self.assertIsNone(coach.load_plan("cle"))  # extraction d'une autre version : à refaire
        coach.save_plan(dict(data, version=1))  # la version d'avant reste affichée, en attendant d'être relue
        self.assertEqual(coach.load_plan("cle")["version"], 1)


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


def ev_of(values: dict, sizes: dict = None, raises: dict = None) -> dict:
    """Un résumé d'EV (coach.ev_summary) : {case: (part de la range, {action: valeur}[, {action: part}])} ; sans
    parts, le solveur joue la meilleure action de la case (sinon, ces parts, sans meilleure main dans la case)."""
    classes = sorted({c for v in values.values() for c in v[1]}, key=coach.ORDER.index)
    cells = {}
    for k, v in values.items():
        r, vals = v[0], v[1]
        f = v[2] if len(v) > 2 else {max(vals, key=vals.get): r}
        cells[k] = {"r": r, "e": dict(vals), "f": dict(f),
                    "s": max(vals.values()) if len(v) == 2 else sum(p / r * vals[c] for c, p in f.items())}
    return {"classes": classes, "sizes": sizes or {}, "raises": raises or {}, "b": cells}


class LevelsTest(unittest.TestCase):
    def test_simplify(self):
        # flops hauts et secs (petite mise seulement) : tout le monde gagne à miser ; flops bas et connectés (petite et
        # grosse mise) : à checker, sauf les nuts qui misent gros
        high = ev_of({"e4|nuts": (0.2, {"check": 0.10, "small": 0.12}),
                      "e1|air": (0.8, {"check": 0.01, "small": 0.03})}, {"small": 33})
        low = ev_of({"e4|nuts": (0.2, {"check": 0.10, "small": 0.12, "big": 0.13}),
                     "e1|air": (0.8, {"check": 0.05, "small": 0.02, "big": 0.00})}, {"small": 25, "big": 100})
        entries = [("haut-sec", high), ("haut-sec", high), ("bas-connecte", low)]
        self.assertEqual(coach._substitute("big", ["check", "small"]), "small")  # la taille la plus proche
        self.assertIsNone(coach._substitute("raise", ["check", "small"]))
        self.assertEqual(coach._eligible([["check", "small"], ["check", "small", "big"]]), ["check", "small"])
        self.assertEqual(coach._eligible([["check", "small"], ["check", "big"]]), ["check", "small", "big"])
        one = coach.simplify(entries, by_flop=False, hands="range")
        self.assertEqual(one["rules"], {"tous": {"range": {"class": "small", "share": 1.0}}})  # (pas de grosse partout)
        # miser petit partout : rien perdu sur les flops hauts, 0,18 - 0,14 = 4 % du pot sur le flop bas
        self.assertAlmostEqual(one["loss"], 0.04 / 3)
        by_flop = coach.simplify(entries, by_flop=True, hands="range")
        self.assertEqual({c: r["range"]["class"] for c, r in by_flop["rules"].items()},
                         {"haut-sec": "small", "bas-connecte": "check"})
        self.assertAlmostEqual(by_flop["by"]["bas-connecte"], 0.03)  # les nuts checkent aussi
        self.assertEqual(by_flop["flops"], {"haut-sec": 2, "bas-connecte": 1})
        boxes = coach.simplify(entries, by_flop=True, hands="equite")  # une action par boîte d'équité
        self.assertEqual(boxes["rules"]["bas-connecte"], {"e4": {"class": "big", "share": 0.2},
                                                          "e1": {"class": "check", "share": 0.8}})
        self.assertAlmostEqual(boxes["loss"], 0.0)
        self.assertEqual([c for c, _ in coach.pure_options(entries)], ["small", "check"])
        levels = coach.levels(entries, "attack")
        self.assertEqual([(st["key"], st["kind"]) for st in levels["steps"]],
                         [("une", "pure"), ("flop", "pure"), ("equite", "mix"), ("detail", "mix")])
        self.assertEqual(levels["sizes"], {"small": 33, "big": 100})  # la médiane des flops
        self.assertGreaterEqual(levels["steps"][0]["loss"], levels["steps"][1]["loss"])
        self.assertEqual([st["key"] for st in coach.levels(entries, "defense")["steps"]], ["une", "flop", "detail"])
        self.assertIsNone(coach.levels([], "attack"))
        self.assertEqual(coach.class_text("small", {"small": 33}), "petite mise (33 %)")
        self.assertEqual(coach.class_text("raise", {"raise": 3.5}), "relance (×3,5)")

    def test_mixes(self):
        # à la turn, sur une brique : les nuts misent gros, « rien » bluffe 30 % de ses mains, la hauteur As checke
        brick = dict(ev_of({"e4|nuts": (0.3, {"check": 0.10, "small": 0.15, "big": 0.20}),
                            "e1|air": (0.5, {"check": 0.0, "small": 0.02, "big": 0.03},
                                       {"check": 0.35, "small": 0.05, "big": 0.10}),
                            "e1|high": (0.2, {"check": 0.02, "small": 0.01, "big": 0.0})},
                           {"small": 33, "big": 75}), n=4)
        found = coach.mixes([("brick", brick)], by_flop=True)
        rule = found["rules"]["brick"]
        # une seule taille : la grosse (en petite, les nuts perdraient 5 % du pot)
        self.assertEqual((rule["size"], rule["sizes"], rule["facing"]), ("big", {"big": 75}, False))
        self.assertAlmostEqual(found["loss"], 0.0)  # (les bluffs de « rien » joués gros valent plus que le mélange)
        self.assertAlmostEqual(rule["freq"]["big"], 0.45)
        e1 = rule["boxes"]["e1"]
        self.assertAlmostEqual(e1["r"], 0.7)
        self.assertAlmostEqual(e1["f"]["big"], 0.15 / 0.7)
        self.assertEqual([x["key"] for x in e1["families"]], ["air", "high"])  # qui mise d'abord : l'air, pas l'As
        self.assertAlmostEqual(e1["families"][0]["f"]["big"], 0.3)
        self.assertEqual([x["key"] for x in found["families"]["e1"]], ["air", "high"])
        detail = coach.mixes([("brick", brick)], by_flop=True, sizes="solveur")["rules"]["brick"]
        self.assertIsNone(detail["size"])
        self.assertEqual(detail["sizes"], {"small": 33, "big": 75})
        # face au check-raise : une relance, sa taille en multiple de la mise
        facing = ev_of({"e4|nuts": (0.4, {"fold": 0.0, "call": 0.2, "raise": 0.3}),
                        "e1|air": (0.6, {"fold": 0.0, "call": -0.1, "raise": -0.2})}, raises={"raise": 3.0})
        rule = coach.mixes([("haut-sec", facing)], by_flop=False)["rules"]["tous"]
        self.assertEqual((rule["size"], rule["sizes"], rule["facing"]), ("raise", {"raise": 3.0}, True))
        self.assertAlmostEqual(rule["boxes"]["e1"]["f"]["fold"], 1.0)

    def test_streets(self):
        # la suite d'un niveau, toujours selon la carte : une taille par type de carte, les fractions par boîte
        flop = ev_of({"e4|nuts": (0.2, {"check": 0.10, "small": 0.12}),
                      "e1|air": (0.8, {"check": 0.01, "small": 0.03})}, {"small": 33})
        brick = dict(ev_of({"e4|nuts": (0.3, {"check": 0.10, "big": 0.20}),
                            "e1|air": (0.7, {"check": 0.02, "big": 0.00})}, {"big": 75}), n=3)
        over = dict(ev_of({"e4|nuts": (0.3, {"check": 0.10, "big": 0.12}),
                           "e1|air": (0.7, {"check": 0.00, "big": 0.04})}, {"big": 80}), n=1)
        later = {"barrel": [("brick", brick), ("over", over)], "delayed": []}
        levels = coach.levels([("haut-sec", flop)], "attack", later)
        one = levels["steps"][0]
        self.assertEqual(set(one["streets"]), {"barrel"})  # (sans entrée : pas de suite)
        barrel = one["streets"]["barrel"]
        self.assertEqual((barrel["kind"], barrel["by_flop"], barrel["hands"]), ("mix", True, "equite"))
        self.assertEqual(barrel["flops"], {"brick": 1, "over": 1})
        self.assertEqual({k: (r["size"], r["sizes"]) for k, r in barrel["rules"].items()},
                         {"brick": ("big", {"big": 75}), "over": ("big", {"big": 80})})  # la taille selon la carte
        self.assertAlmostEqual(barrel["rules"]["over"]["boxes"]["e1"]["f"]["big"], 1.0)
        self.assertAlmostEqual(barrel["loss"], 0.0)
        self.assertEqual(levels["steps"][3]["streets"]["barrel"]["sizes_mode"], "solveur")

    def test_sizes_by_card(self):
        mix = coach.size_mix({"check": 0.4, "small": 0.15, "big": 0.45}, [33, 75, 120])
        self.assertEqual([(m["class"], round(m["share"], 2), m["pcts"]) for m in mix],
                         [("big", 0.75, [120]), ("small", 0.25, [33])])
        self.assertEqual(coach.size_mix({"check": 1.0}), [])


class RefineTest(unittest.TestCase):
    """Affiner la suite d'un niveau : quelques flops résolus de nouveau avec le flop du niveau imposé (un verrou)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_sample_and_edits(self):
        boards = ("Ks7d2c", "Kd8s3h", "9s8d6c", "7h7c3d", "Ah9d4c")
        data = [{"id": f"spot:srp:{b}", "board": cards_of(b)} for b in boards]
        sample = coach.refine_sample(data, n=4)  # un flop par type à tour de rôle, les types les plus fréquents d'abord
        self.assertEqual([d["id"][9:] for d in sample], ["Ah9d4c", "9s8d6c", "7h7c3d", "Kd8s3h"])
        spot = StudySpot("srp", list(BOARD), plan={})
        one = {"key": "une", "kind": "pure", "by_flop": False, "hands": "range",
               "rules": {"tous": {"range": {"class": "small", "share": 1.0}}}}
        edits = coach.refine_edits(spot, one, {"small": 33})
        self.assertEqual(edits["plan"], {"bet:fi:": [33]})  # l'arbre n'a plus que la taille du niveau
        lock = edits["locks"][0]
        self.assertEqual(lock["path"], [{"type": "action", "index": 0}])  # la c-bet, après le check de la BB
        self.assertEqual({tuple(f) for f in lock["combos"].values()}, {(0, 1)})  # toute la range mise
        self.assertFalse(any({c[:2], c[2:]} & set(BOARD) for c in lock["combos"]))
        check = dict(one, rules={"tous": {"range": {"class": "check", "share": 1.0}}})
        self.assertEqual(coach.refine_edits(spot, check, {}), {"plan": {"bet:fi:": []}, "locks": [], "auto": {}})
        # en pot 4bet (pot 52, tapis 74) : une mise d'au moins 85 % du tapis devient le tapis
        spot4 = StudySpot("4bet", list(BOARD), plan={})
        over = dict(one, rules={"tous": {"range": {"class": "overbet", "share": 1.0}}})
        edits = coach.refine_edits(spot4, over, {"overbet": 125})
        self.assertEqual(edits["plan"], {"bet:fi:": [125]})
        self.assertEqual({tuple(f) for f in edits["locks"][0]["combos"].values()}, {(0, 1)})
        # la signature d'une action pure : la règle de la case et les tailles jouées, pas la part de la range
        sig = coach.refine_signature(one, "haut-sec", {"small": 33})
        self.assertEqual(sig, coach.refine_signature(one, "haut-sec", {"small": 33, "big": 100}))
        self.assertNotEqual(sig, coach.refine_signature(one, "haut-sec", {"small": 25}))
        # des fractions : chaque case joue celles de sa famille dans sa boîte (sa boîte, sinon toute la range)
        mix = coach.mixes([("haut-sec", ev_of({"e4|nuts": (0.3, {"check": 0.1, "big": 0.2}),
                                                "e1|air": (0.7, {"check": 0.0, "big": 0.01},
                                                           {"check": 0.49, "big": 0.21})}, {"big": 75}))],
                          by_flop=True)
        step = dict(mix, by_flop=True, hands="equite", sizes_mode="une")
        self.assertEqual(coach.play_of(step, "haut-sec", "e4|nuts"), {"big": 1.0})
        self.assertAlmostEqual(coach.play_of(step, "haut-sec", "e1|air")["big"], 0.3)
        self.assertAlmostEqual(coach.play_of(step, "haut-sec", "e1|fd")["big"], 0.3)  # (sa boîte)
        self.assertAlmostEqual(coach.play_of(step, "haut-sec", None)["big"], 0.51)  # (toute la range)
        self.assertEqual(coach._mix_plan(spot, step), {"bet:fi:": [75]})  # la seule taille de c-bet du niveau
        self.assertEqual(coach._mix_plan(spot, dict(step, sizes_mode="solveur")), {})
        sig = coach.refine_signature(step, "haut-sec")
        step["rules"]["haut-sec"]["boxes"]["e1"]["families"][0]["f"]["big"] = 0.4
        self.assertNotEqual(sig, coach.refine_signature(step, "haut-sec"))

    def test_refined_step(self):
        step = {"key": "une", "kind": "pure", "by_flop": False, "hands": "range",
                "rules": {"tous": {"range": {"class": "small", "share": 1.0}}}}
        turn = dict(ev_of({"e4|nuts": (0.3, {"check": 0.10, "big": 0.20}),
                           "e1|air": (0.7, {"check": 0.02, "big": 0.00})}, {"big": 75}), n=20)
        fresh = {"category": "haut-sec", "board": ["Ks", "7d", "2c"], "player": 1, "pot": 5.0,
                 "signature": coach.refine_signature(step, "haut-sec", {"small": 33}), "root_ev": [2.0, 3.0],
                 "base_ev": [1.9, 3.1], "exploit": [1.0, 1.2], "nodes": {"barrel": {"evs": {"brick": turn}}}}
        stale = dict(fresh, signature="avant", board=["9s", "8d", "6c"])
        signature = lambda category: coach.refine_signature(step, category, {"small": 33})  # noqa: E731
        found = coach._refined_step(step, [fresh, stale], signature, "attack")
        self.assertEqual((found["flops"], found["stale"], found["boards"]), (1, 1, [["Ks", "7d", "2c"]]))
        self.assertAlmostEqual(found["loss"], 0.1 / 5)  # l'écart d'EV à la racine, en part du pot
        self.assertAlmostEqual(found["margin"], 0.012)
        rule = found["streets"]["barrel"]["rules"]["brick"]  # la suite relue, selon la carte
        self.assertEqual((rule["size"], rule["sizes"]), ("big", {"big": 75}))
        self.assertEqual(rule["boxes"]["e4"]["f"], {"big": 1.0})
        self.assertIsNone(coach._refined_step(step, [], signature, "attack"))
        self.assertEqual(coach._refined_step(step, [stale], signature, "attack"),
                         {"flops": 0, "stale": 1, "boards": []})

    def test_locks_on_the_tree(self):
        # un nœud de l'arbre lu avant : chaque main y joue les fractions de sa case (son équité ici, sa famille)
        node = {"type": "action", "player": 1, "pot": 10.0, "board": list(BOARD),
                "actions": [{"kind": "check", "amount": 0.0}, {"kind": "bet", "amount": 3.3},
                            {"kind": "bet", "amount": 7.0}, {"kind": "bet", "amount": 7.9}],
                "hands": [[], [["AhAd", 1.0, 0.9], ["8h8c", 1.0, 0.6], ["QhJh", 0.0, 0.1], ["6h5h", 1.0, None]]]}
        self.assertEqual(coach._class_index(node, "medium", {"medium": 75}), 3)  # la plus proche de 75 % du pot
        self.assertEqual(coach._class_index(node, "medium", {}), 2)
        self.assertEqual(coach._class_index(node, "big", {}), 2)  # (pas de grosse mise : la taille la plus proche)
        self.assertEqual(coach._class_index(node, "call", {}), 0)  # (pas de call ici : check)
        plays = {"e4|overpair": {"small": 1.0}, "e3|midpair": {"check": 0.75, "medium": 0.25}, "e1|air": {}}
        lock = coach._node_lock(node, [{"type": "action", "index": 0}],
                                lambda cell: plays.get(cell, {"medium": 1.0}) if cell else {"check": 1.0}, {})
        self.assertEqual(lock["combos"], {"AhAd": [0, 1, 0, 0], "8h8c": [0.75, 0, 0.25, 0], "QhJh": [1, 0, 0, 0],
                                          "6h5h": [1, 0, 0, 0]})  # (sans règle : check ; sans équité : la range)
        self.assertIsNone(coach._node_lock(dict(node, actions=node["actions"][:1]), [], lambda cell: {}, {}))
        facing = {"type": "action", "player": 0, "pot": 10.0, "board": list(BOARD),
                  "actions": [{"kind": "fold", "amount": 0.0}, {"kind": "call", "amount": 3.3},
                              {"kind": "raise", "amount": 90.0, "allin": True}], "hands": [[], []]}
        self.assertEqual(coach._class_index(facing, "raise", {}), 2)  # relancer : ici, le tapis


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
            levels = plan["levels"]
            self.assertEqual((levels["read"], levels["older"], levels["attack"]["flops"]), (1, 0, 1))
            self.assertEqual(levels["attack"]["steps"][0]["rules"]["tous"]["range"]["class"], "check")
            self.assertTrue(attack["turn"][0]["sizes"])  # la taille préférée à cette carte
            base = plan["baseline"]
            self.assertEqual((base["cbet"], base["fold_cbet"]), (plan["flops"][0]["cbet"], defense["vs_cbet"]["fold"]))
            from analyzer import field
            from analyzer.insights import STAT_DEFS, Finding
            from analyzer.stats import Ratio
            fold = STAT_DEFS["vs_cbet_flop.fold"]
            leaks = [Finding(fold, Ratio(60, 100), "haut", True, fold.high)]
            leaks += [Finding(STAT_DEFS[k], Ratio(20, 100), "bas", True, STAT_DEFS[k].low)
                      for k in ("cbet_flop_ip", "cbet_flop_oop")]
            study = field.Study(["V1", "V2"], "HU", 400, {}, leaks, ("autre", []), [], [], [], {}, {}, "hautes")
            pops = {"HU": {"band": "hautes", "bands": [("micro", 10), ("hautes", 20)],
                           "populations": [{"key": "reg", "label": "Les réguliers", "players": 2, "study": study}]}}
            page = build_coach_page({"missing": 0, "busy": 0}, field=pops)
            for text in ("Plan de jeu suggéré", "Au flop : trois stratégies de c-bet", "Miser range", "Stratégie mixte",
                         "Checker range", "Petite mise (33 % du pot)", 'href="#att-srp-check-petite"',
                         'href="#def-srp-check-petite"', "En attaque · le bouton", "En défense · la BB",
                         "Face au 2e barrel, selon la turn", "la BB mène à la turn (probe)",
                         "Aucun flop résolu ne s'y range : sur ces flops, le bouton mise au plus",
                         "/explorateur/spot%3Asrp%3AKs7d2c", "À la turn, si la c-bet est payée",
                         "selon la stratégie du bouton", "la défense de la BB", "Du plus simple au plus précis",
                         "Niveau 1 · Une seule règle", "Le bouton checke toute sa range, sur tous les flops.",
                         "Face à la c-bet : du plus simple au plus précis", "Taille préférée", "Contre le field",
                         "C-bet un peu plus souvent", "Respecte leurs c-bets, attaque leurs checks",
                         "À la river, après la c-bet payée et la turn checkée",
                         "Face à la mise river après la turn checkée",
                         "River · après la c-bet payée et la turn checkée",
                         'href="?limites=micro#famille=srp&amp;cote=field"', "Les réguliers en heads-up, hautes limites"):
                self.assertIn(text, page)
            self.assertEqual(page.count("Respecte leurs c-bets"), 1)  # la c-bet hors de position : pas en SRP
            coach.save_plan(dict(data, version=1))  # une étude lue par une version d'avant : à relire
            self.assertEqual(coach.missing(), [spot.ident])
            old = {k: v for k, v in data.items() if k != "nodes"}
            old["nodes"] = {k: {kk: vv for kk, vv in n.items() if kk not in ("ev", "evs")}
                            for k, n in data["nodes"].items()}
            coach.save_plan(dict(old, version=1))
            plan = coach.family_plan("srp")
            self.assertEqual((plan["count"], plan["levels"]["older"], plan["levels"]["attack"]), (1, 1, None))
            self.assertIn("le coach doit relire tes études", build_coach_page({"missing": 1, "busy": 0}))
            coach.save_plan(data)
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

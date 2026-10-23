import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer.app.library import Library
from analyzer.app.review_page import build_review_page
from analyzer.parsers import load_hands
from analyzer.theory import review

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_SOLVER = FIXTURES / "fake_solver.py"
POSIX = os.name != "nt"


def step(player, kinds, chosen):
    return {"kind": "action", "player": player, "actions": [{"kind": k} for k in kinds], "chosen": chosen}


CARD = {"kind": "card", "player": None, "actions": [], "chosen": None}


def node(player, history):
    return {"player": player, "history": history + [step(player, [], None)]}


class SituationTest(unittest.TestCase):
    def test_keys(self):
        check, bet, call = (step(0, ["check"], 0), step(1, ["check", "bet"], 1), step(0, ["fold", "call", "raise"], 1))
        self.assertEqual(review.situation(node(1, [check])), "bet:fi:")  # c-bet du bouton
        self.assertEqual(review.situation(node(0, [check, bet])), "face:fo::1")  # BB face à la c-bet
        raise_ = step(0, ["fold", "call", "raise"], 2)
        self.assertEqual(review.situation(node(1, [check, bet, raise_])), "face:fi::2")  # face au check-raise
        turn = [check, bet, call, CARD, step(0, ["check"], 0)]
        self.assertEqual(review.situation(node(1, turn)), "bet:ti:i")  # 2e barrel
        checked = [check, step(1, ["check", "bet"], 0), CARD]
        self.assertEqual(review.situation(node(0, checked)), "bet:to:x")  # probe turn

    def test_labels(self):
        self.assertEqual(review.situation_label("bet:fi:", "srp"), "C-bet")
        self.assertEqual(review.situation_label("face:fo::1", "srp"), "Face à : C-bet")
        self.assertEqual(review.situation_label("face:fi::2", "srp"), "Face à : Check-raise flop")
        self.assertEqual(review.situation_label("bet:to:o", "3bet"), "2e barrel")
        self.assertEqual(review.situation_label("face:ti:o:1", "3bet"), "Face à : 2e barrel")


def decision(who, key, kinds, chosen, range_, ev_loss=None):
    return {"d": 0, "street": "f", "who": who, "key": key, "label": key, "chosen": chosen, "range": range_,
            "actions": [{"kind": k, "label": k} for k in kinds], "played": kinds[chosen], "approx": False,
            "combo": "AhKd" if ev_loss is not None else None, "strategy": [0.5] * len(kinds) if ev_loss is not None else None,
            "evs": None, "ev_loss": ev_loss, "frequency": None, "verdict": None, "pot": 5.0, "board": ["Ks", "7d", "2c"]}


class AggregateTest(unittest.TestCase):
    def setUp(self):
        # Face à la c-bet, il folde 15 fois sur 20 là où le solveur folderait 30 % du temps.
        folds = [decision("V", "face:fo::1", ["fold", "call", "raise"], 0, [0.3, 0.5, 0.2]) for _ in range(15)]
        calls = [decision("V", "face:fo::1", ["fold", "call", "raise"], 1, [0.3, 0.5, 0.2]) for _ in range(5)]
        mine = [decision("H", "bet:fi:", ["check", "bet"], 0, [0.4, 0.6], ev_loss=1.5),
                decision("H", "bet:fi:", ["check", "bet"], 1, [0.4, 0.6], ev_loss=0.0),
                decision("H", "face:fi::2", ["fold", "call", "raise"], 0, [0.5, 0.4, 0.1], ev_loss=0.1)]
        self.digests = [{"hand": "H1", "date": "01/01/2026 10:00", "villain": "Lui", "family": "srp",
                         "decisions": folds + calls + mine}]

    def test_costly_and_groups(self):
        costly = review.costly(self.digests, "H")
        self.assertEqual([d["ev_loss"] for _, d in costly], [1.5])  # 0,1 bb n'est pas une erreur
        groups = {g["key"]: g for g in review.by_situation(self.digests, "H")}
        self.assertEqual((groups["bet:fi:"]["n"], groups["bet:fi:"]["errors"], groups["bet:fi:"]["lost"]), (2, 1, 1.5))
        self.assertAlmostEqual(groups["bet:fi:"]["expected"]["aggressive"], 1.2)

    def test_hand_summary(self):
        """Une main analysée dans les listes de mains : son EV théorique (ta première décision), l'EV perdue, tes
        erreurs et ta décision la plus chère."""
        mine = [dict(d, d=k) for k, d in enumerate(self.digests[0]["decisions"]) if d["who"] == "H"]
        mine[0]["evs"] = [2.5, None]  # check : 2,5 bb (la mise jouée : hors de l'arbre)
        summary = review.hand_summary({"decisions": mine})
        self.assertEqual(summary, {"ev": 2.5, "lost": 1.6, "errors": 1, "d": mine[0]["d"]})  # la c-bet checkée
        clean = review.hand_summary({"decisions": [dict(mine[1], d=3)]})
        self.assertEqual(clean, {"ev": None, "lost": 0.0, "errors": 0, "d": None})

    def test_mixed_action_costs_nothing(self):
        # Le solveur paie 35 % du temps avec cette main : l'écart d'EV (nœud peu convergé) ne compte pas.
        mixed = dict(decision("H", "face:ri:oo:2", ["fold", "call"], 1, [0.6, 0.4], ev_loss=16.6), frequency=0.35)
        rare = dict(decision("H", "bet:ri:ii", ["check", "bet"], 0, [0.1, 0.9], ev_loss=12.3), frequency=0.03)
        digests = [{"hand": "H2", "date": "", "villain": "Lui", "family": "4bet", "decisions": [mixed, rare]}]
        self.assertEqual([review.loss(d) for d in (mixed, rare)], [0.0, 12.3])
        self.assertEqual([d["ev_loss"] for _, d in review.costly(digests, "H")], [12.3])
        groups = {g["key"]: g for g in review.by_situation(digests, "H")}
        self.assertEqual((groups["face:ri:oo:2"]["errors"], groups["face:ri:oo:2"]["lost"]), (0, 0.0))

    def test_upgrade_old_digest(self):
        # Résumé d'avant la version 2 : J8o mise pot au flop 0,6 % du temps ; au turn, ses « 33 % » sont un reste.
        flop = dict(decision("H", "bet:fo:", ["check", "bet", "bet"], 2, [0.5, 0.2, 0.3], ev_loss=3.04),
                    strategy=[0.838, 0.156, 0.006], evs=[13.98, 13.8, 10.94], frequency=0.006, verdict="écart")
        turn = dict(decision("H", "bet:to:o", ["check", "bet", "bet"], 2, [0.4, 0.2, 0.4], ev_loss=3.19),
                    strategy=[0.398, 0.272, 0.33], evs=[27.98, 25.62, 24.79], frequency=0.33, verdict="secondaire")
        self.assertEqual(review.loss(turn), 0.0)
        data = review.upgrade({"decisions": [flop, turn]})
        self.assertEqual(data["v"], review.VERSION)
        self.assertNotIn("settled", flop)  # première décision : la main est là
        self.assertEqual((turn["strategy"], turn["frequency"], turn["verdict"], turn["settled"]), ([1.0, 0.0, 0.0], 0.0, "écart", True))
        self.assertEqual(review.loss(turn), 3.19)
        self.assertIs(review.upgrade(data), data)

    def test_villain_deviation_and_exploit(self):
        group = review.by_situation(self.digests, "V")[0]
        devs = {d["category"]: d for d in review.deviations(group)}
        self.assertEqual((devs["fold"]["observed"], round(devs["fold"]["expected"], 2)), (0.75, 0.3))
        self.assertEqual(devs["fold"]["confidence"], "solide")
        self.assertIn("folde trop", review.exploit(group, devs["fold"], villain=True))
        self.assertIn("aggressive", devs)  # il ne relance jamais (théorie : 20 %)
        self.assertEqual(review.deviations(dict(group, n=5)), [])  # trop peu d'occurrences


@unittest.skipUnless(POSIX, "faux solveur : script exécutable POSIX")
class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        env = {"ANALYZER_DB": "", "ANALYZER_HOME": str(self.folder / "home"), "ANALYZER_SOLVER": str(FAKE_SOLVER)}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.hands = load_hands([FIXTURES / "betclic_sample.txt"])

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_analyze_collect_and_pages(self):
        done, todo = review.collect(self.hands, "Hero")
        self.assertEqual((len(done), len(todo)), (0, 2))  # SRP et pot 3bet ; tapis préflop et limp exclus
        logs = []
        self.assertEqual(review.analyze(self.hands, "Hero", log=logs.append), 2)
        done, todo = review.collect(self.hands, "Hero")
        self.assertEqual((len(done), len(todo)), (2, 0))
        digest = next(g for g in done if g["hand"] == "HAND02")
        self.assertEqual((digest["family"], digest["villain"]), ("3bet", "Villain"))
        keys = [(d["who"], d["key"]) for d in digest["decisions"]]
        self.assertIn(("H", "bet:fo:"), keys)  # la BB 3betteuse c-bette (ou checke) au flop
        self.assertTrue(all(len(d["actions"]) > 1 for d in digest["decisions"]))
        self.assertTrue(any(d["who"] == "V" and d["combo"] for d in digest["decisions"]))  # cartes montrées
        from analyzer import db
        stored = db.current().all("SELECT main, LENGTH(donnees) FROM analyses ORDER BY main")
        self.assertEqual([main for main, _ in stored], ["HAND01", "HAND02"])  # gardés dans la base
        self.assertLess(max(size for _, size in stored), 20_000)  # compressés
        from analyzer import leaks
        from analyzer.app import solver_pages
        page = solver_pages.panel_html("HU", leaks.solver_review(self.hands, "Hero"), {}, "/api/revue", "/moi")
        for text in ("<b>2</b> mains analysées sur 2", "Les erreurs qui coûtent le plus", "Les erreurs récurrentes",
                     'data-sv="done">Voir les 2 mains analysées', 'href="/moi/solveur"'):
            self.assertIn(text, page)
        villain_page = build_review_page(self.hands, "Hero", "Villain")
        for text in ("2 / 2</b> mains analysées", "Ses écarts à exploiter", "Les erreurs récurrentes"):
            self.assertIn(text, villain_page)
        self.assertNotIn("Ses écarts à exploiter", page)

    def test_stake_charts_ranges(self):
        """Tes charts des hautes limites deviennent les ranges du solveur pour ces mains (grosse blinde à 5) ; une
        main déjà analysée avec les ranges de la référence garde son analyse."""
        from analyzer.theory import postflop, stake_charts
        hand = next(h for h in self.hands if h.hand_id == "HAND01")
        before = postflop.build_spot(hand, "Hero")
        self.assertEqual(review.analyze([hand], "Hero", log=lambda m: None), 1)
        stake_charts.save("HU", "hautes", {"source": "Essai", "nodes": {"sb_open": {"raise": "AA,KK,QQ,AKs,AKo"}}})
        after = postflop.build_spot(hand, "Hero")
        self.assertEqual(postflop.build_spot(hand, "Hero", stakes=False).ranges, before.ranges)
        self.assertNotEqual(after.ranges[hand.button], before.ranges[hand.button])
        self.assertLess(len(after.ranges[hand.button]), len(before.ranges[hand.button]))
        done, todo = review.collect([hand], "Hero")
        self.assertEqual((len(done), len(todo)), (1, 0))  # l'analyse d'avant compte toujours

    def test_library_partial_queue(self):
        """N'analyser qu'une partie des mains : les plus chères d'abord, une seule ici."""
        lib = Library(FIXTURES)
        try:
            orders = lib.review_orders()
            self.assertEqual(set(orders), {"leaks", "pertes", "pots"})
            costliest = min(self.hands, key=lambda h: h.net("Hero") / h.bb if h.hand_id in ("HAND01", "HAND02")
                            else 0).hand_id
            self.assertEqual(orders["pertes"][0][0], costliest)
            self.assertTrue(all(minutes > 0 for _, minutes in orders["pertes"]))
            lib.review_state(start=True, order="pertes", limit=1)
            import time
            deadline = time.time() + 30
            while lib.review_state()["busy"] and time.time() < deadline:
                time.sleep(0.05)
            done, todo = review.collect(self.hands, "Hero")
            self.assertEqual(([g["hand"] for g in done], len(todo)), ([costliest], 1))
            self.assertEqual(lib.review_orders(table_format="HU")["pertes"], [orders["pertes"][1]])
            page = lib.self_page("solveur")  # le bilan du solveur : le heads-up seulement (pas d'autre format)
            for text in ('class="fs-run" data-api="/api/revue"', "&quot;pertes&quot;: {&quot;n&quot;: 1",
                         '<h2 id="hu">Heads-up</h2>', "sur 2 allées au flop"):
                self.assertIn(text, page)
            self.assertNotIn('class="fs-format"', page)  # un seul format : pas de choix
            panel = lib.solver_panel("HU")
            self.assertIn("<b>1</b> mains analysées sur 2", panel)
            with self.assertRaises(Exception):
                lib.solver_panel("ring")  # pas de mains aux tables à plusieurs
        finally:
            lib.solves.shutdown()

    def test_library_hand_lists(self):
        """Mes mains : chaque main avec ce qu'en dit le solveur, le filtre et le tri « Solveur », et les mains de la
        sélection mises en file, dans l'ordre de la liste."""
        import time
        lib = Library(FIXTURES)
        try:
            todo = lib.spots_search({"sv": "todo"})
            self.assertEqual((todo["total"], todo["solver"]["todo"], todo["solver"]["done"]), (2, 2, 0))
            self.assertTrue(all(r["sv"]["todo"] > 0 for r in todo["rows"]))
            self.assertGreater(todo["solver"]["minutes"], 0)
            self.assertEqual(lib.spots_search({"sv": "done"})["total"], 0)
            everything = lib.spots_search({})
            self.assertGreater(everything["total"], 2)  # les mains sans flop n'ont pas de statut
            self.assertTrue(any("sv" not in r for r in everything["rows"]))
            first = lib.spots_solve({"sv": "todo", "sort": "pot"}, "resoudre", limit=1)
            self.assertEqual((first["total"], first["done"]), (2, 0))
            deadline = time.time() + 30
            while lib.spots_solve({}, "etat")["busy"] and time.time() < deadline:
                time.sleep(0.05)
            done = lib.spots_search({"sv": "done"})
            self.assertEqual(done["total"], 1)
            biggest = lib.spots_search({"sv": "todo", "sort": "pot"})
            self.assertNotIn(done["rows"][0]["id"], [r["id"] for r in biggest["rows"]])
            self.assertEqual(set(done["rows"][0]["sv"]), {"ev", "lost", "errors", "d"})
            self.assertEqual(lib.spots_search({"sort": "evloss"})["rows"][0]["id"], done["rows"][0]["id"])
            state = lib.spots_solve({}, "etat")
            self.assertEqual((state["total"], state["done"], state["busy"]), (2, 1, 0))
            other = biggest["rows"][0]["id"]
            lib.spots_solve({}, "resoudre", ids=[other, "inconnue"])  # par ses numéros (tables à plusieurs aussi)
            deadline = time.time() + 30
            while lib.spots_solve({}, "etat", ids=[other])["busy"] and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(lib.spots_search({"sv": "done"})["solver"]["done"], 2)
        finally:
            lib.solves.shutdown()

    def test_library_queue(self):
        lib = Library(FIXTURES)
        try:
            state = lib.review_state()
            self.assertEqual((state["total"], state["done"], state["busy"]), (2, 0, 0))
            lib.review_state(start=True)
            import time
            deadline = time.time() + 30
            while lib.review_state()["busy"] and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(lib.review_state("Villain")["done"], 2)
            self.assertIn("Face au solveur", lib.player_page("Villain", "solveur"))
            self.assertIn("<b>2</b> mains analysées sur 2", lib.solver_panel("HU"))
            self.assertIn('<div class="value">2</div><div class="sub">sur 2 allées au flop', lib.self_page("solveur"))
            self.assertEqual(lib.review_state(table_format="HU")["done"], 2)
            self.assertEqual(lib.review_state(table_format="ring"), dict(lib.review_state(table_format="ring"),
                                                                          total=0, done=0))
            # le résumé survit au cache des résultats (clé du spot seul)
            from analyzer import db
            db.current().execute("DELETE FROM documents WHERE type = 'resolution'")
            self.assertEqual(review.collect(self.hands, "Hero")[0][0]["hand"] in ("HAND01", "HAND02"), True)
            self.assertEqual(len(review.collect(self.hands, "Hero")[1]), 0)
            json.dumps(lib.review_cancel())
        finally:
            lib.solves.shutdown()


if __name__ == "__main__":
    unittest.main()

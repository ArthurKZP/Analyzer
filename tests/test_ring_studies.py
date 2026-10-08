"""Spots d'étude 6-max : un autre jeu que le heads-up (ranges des charts 6-max), sur les mêmes flops."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from analyzer.parsers import parse_text
from analyzer.theory import postflop, ring_ranges, sizing, studyspots

SITES = Path(__file__).parent / "sites"
FAKE_SOLVER = Path(__file__).parent / "fixtures" / "fake_solver.py"

# Ranges synthétiques (pas celles d'un site), une ligne par famille testée.
LINES = {
    "BTN:raise BB:call": {"BTN": "AA,KK,QQ,AKs,KQs,76s", "BB": "TT,99,AJs,KQo,65s"},
    "BTN:raise BB:raise BTN:call": {"BTN": "QQ,JJ,AQs,KQs", "BB": "AA,KK,AKs,A5s"},
    "SB:raise BB:call": {"SB": "AA,K9s,Q8o,76s,54s", "BB": "JJ,ATs,KTo,98s"},
    "SB:raise BB:raise SB:call": {"SB": "TT,AQs,KJs", "BB": "AA,KK,AKo,A4s"},
    "SB:raise BB:raise SB:raise BB:call": {"SB": "AA,KK,A5s", "BB": "QQ,AKs"},
}


class RingHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def write_charts(self):
        ring_ranges.save_solution("6-max", {"format": "6-max", "source": "Charts de test — synthétiques",
                                            "lines": {k: {"ranges": v} for k, v in LINES.items()}})


class FamiliesTest(unittest.TestCase):
    def test_families(self):
        fams = studyspots.RING_FAMILIES
        self.assertEqual(len(fams), 13)
        self.assertNotIn("6max_bb_btn_srp", studyspots.FAMILIES)  # le heads-up reste à part
        sb_bb = fams["6max_sb_bb_srp"]
        self.assertEqual((sb_bb["oop"], sb_bb["ip"], sb_bb["pot"], sb_bb["stack"], sb_bb["structure"]),
                         ("SB", "BB", 5.0, 97.5, "srp_oop"))
        self.assertEqual((fams["6max_sb_bb_3bet"]["pot"], fams["6max_sb_bb_3bet"]["structure"]), (15.0, "3bet_ip"))
        self.assertEqual((fams["6max_sb_bb_4bet"]["pot"], fams["6max_sb_bb_4bet"]["structure"]), (40.0, "4bet_oop"))
        self.assertEqual((fams["6max_sb_btn_3bet"]["pot"], fams["6max_sb_btn_3bet"]["stack"]), (21.0, 90.0))  # BB morte
        self.assertEqual((fams["6max_bb_btn_srp"]["pot"], fams["6max_bb_co_4bet"]["pot"]), (5.5, 44.5))  # SB morte
        self.assertEqual(fams["6max_bb_co_3bet"]["label"], "6-max, BB contre CO, pot 3bet : open du CO à 2,5 bb, 3bet "
                                                           "de la BB à 10 bb, call du CO, 100 bb")
        self.assertEqual(studyspots.flop_set("6max_bb_btn_srp"), studyspots.flop_set("srp"))  # les mêmes flops

    def test_labels(self):
        self.assertEqual(sizing.label("raise:fi::0", "6max_sb_bb_srp"), "Relance de la BB face à la c-bet")
        self.assertEqual(sizing.label("bet:fi:", "6max_bb_co_3bet"), "Stab flop du CO")
        self.assertEqual(sizing.label("bet:fi:", "srp"), "C-bet")
        self.assertEqual(sizing.label("bet:fi:", "srp", ("BB", "HJ")), "C-bet")
        self.assertEqual(sizing.rename_roles("Relance du BTN face au check-raise de la BB", "BB", "CO"),
                         "Relance du CO face au check-raise de la BB")
        self.assertEqual(studyspots.SUMMARY["6max_sb_bb_3bet"][1][0], "C-bet de la BB")  # la BB 3bette en position
        for family in ("6max_sb_bb_srp", "6max_sb_bb_3bet", "6max_sb_bb_4bet"):
            keys = {s.key for s in sizing.situations(family)}
            self.assertTrue(keys)
            agg = "o" if studyspots.RING_FAMILIES[family]["oop_initiative"] else "i"
            self.assertIn(f"bet:f{agg}:", keys)  # la c-bet de celui qui a l'initiative

    def test_hand_families(self):
        size = studyspots.size_families
        self.assertEqual(size(True, "BB", "BTN", "SRP", False), ["srp"])
        self.assertEqual(size(False, "BB", "BTN", "SRP", False),
                         ["6max_bb_btn_srp", "6max_bb_co_srp", "6max_bb_hj_srp", "srp"])
        self.assertEqual(size(False, "BB", "HJ", "SRP", False),  # sa série, puis celles de même structure
                         ["6max_bb_hj_srp", "6max_bb_btn_srp", "6max_bb_co_srp", "srp"])
        self.assertEqual(size(False, "BB", "UTG", "SRP", False)[0], "6max_bb_btn_srp")  # pas de série BB contre UTG
        self.assertEqual(size(False, "SB", "BB", "SRP", True), ["6max_sb_bb_srp"])  # pas d'équivalent heads-up
        self.assertEqual(size(False, "BB", "CO", "pot 3bet", True)[:2], ["6max_bb_co_3bet", "6max_sb_btn_3bet"])
        self.assertEqual(size(True, "BB", "BTN", "pot 3bet", False), [])  # 3bet du bouton en heads-up : pas couvert


class RingSpotTest(RingHome):
    def test_without_charts(self):
        with self.assertRaises(postflop.Unsupported) as err:
            studyspots.StudySpot("6max_bb_btn_srp", studyspots.cards_of("KsKd4c"))
        self.assertIn("Pas de ranges 6-max", str(err.exception))
        self.assertTrue(studyspots.is_ident("spot:6max_bb_btn_srp:KsKd4c"))
        self.assertTrue(studyspots.is_ident("spot:6max_bb_utg_srp:KsKd4c"))  # hors des séries, depuis l'explorateur
        self.assertFalse(studyspots.is_ident("spot:6max_btn_bb_srp:KsKd4c"))
        from analyzer.app.studies import build_studies_page
        self.assertIn("Charge d'abord tes charts 6-max", build_studies_page(section="6max"))

    def test_spot(self):
        self.write_charts()
        spot = studyspots.parse_ident("spot:6max_sb_bb_srp:KsKd4c")
        self.assertEqual((spot.oop, spot.ip, spot.pot_bb, spot.stack_bb), ("SB", "BB", 5.0, 97.5))
        self.assertEqual(spot.ranges["SB"]["K9s"], 1.0)
        self.assertEqual(spot.context, "6-max|SB:raise BB:call")
        request = spot.request()
        self.assertEqual(request["spot"]["tree"]["oop"][0]["bet"], [{"PotPct": 33.0}])  # la SB a l'initiative
        self.assertNotIn("plan", request)  # pas encore de tailles choisies

    @unittest.skipIf(os.name == "nt", "faux solveur : script exécutable POSIX")
    def test_choose_and_list(self):
        self.write_charts()
        with mock.patch.dict(os.environ, {"ANALYZER_SOLVER": str(FAKE_SOLVER)}):
            for family in ("6max_sb_bb_srp", "6max_sb_bb_3bet", "6max_sb_bb_4bet"):  # les trois structures nouvelles
                result = studyspots.choose_sizes(family, "KsKd4c", lambda m: None)
                agg = "o" if studyspots.RING_FAMILIES[family]["oop_initiative"] else "i"
                self.assertIn(f"bet:f{agg}:", result["plan"])
            spot = studyspots.StudySpot("6max_sb_bb_srp", studyspots.cards_of("KsKd4c"))
            self.assertIn("relance de la BB face à la c-bet", spot.menu_text())
            request = spot.request()
            study = postflop.study_path(request)
            study.parent.mkdir(parents=True, exist_ok=True)
            study.write_bytes(b"x")
            spot.write_meta(request, {"iterations": 5, "exploit_pct": 0.5, "seconds": 1})
        self.assertEqual(studyspots.spot_studies(), {})  # le heads-up ne voit pas les études 6-max
        self.assertIn(spot.ident, studyspots.spot_studies(("6max_sb_bb_srp",)))
        self.assertEqual(studyspots.stale_spot_studies(), [])
        from analyzer.app.studies import build_studies_page
        page = build_studies_page(section="6max")
        self.assertIn("6-max · SB contre BB · SRP", page)
        self.assertIn("Résoudre tous les flops 6-max manquants", page)
        self.assertIn("spot:6max_sb_bb_srp:KsKd4c", page)

    def test_ring_hand_uses_ring_sizes(self):
        hand = parse_text((SITES / "winamax.txt").read_text(encoding="utf-8"))[0]  # 3-max : BB 3bette le bouton
        ring_ranges.save_solution("3-max", {"format": "3-max", "lines": {
            "BTN:raise BB:raise BTN:call": {"ranges": {"BTN": "AA,KK:0.5,AQs,A9s", "BB": "AA,KK,QQ,AKs"}}}})
        spot = postflop.build_spot(hand, "Hero")
        self.assertEqual((spot.family, spot.size_target), ("3bet", "6max_bb_btn_3bet"))  # pas encore de 6-max
        info = spot.sizes_info()
        self.assertEqual((info["family_title"], info["choose"]), ("heads-up, pot 3bet", "6max_bb_btn_3bet"))
        studyspots.save_selection("6max_bb_btn_3bet", "".join(hand.board[:3]), {"plan": {"bet:fo:": [75]}})
        spot = postflop.build_spot(hand, "Hero")
        self.assertEqual((spot.family, spot.sizes_from["exact"]), ("6max_bb_btn_3bet", True))
        self.assertIsNone(spot.sizes_info()["choose"])
        self.assertEqual(spot.sizes_info()["family_title"], "6-max, BB contre BTN, pot 3bet")

    def test_library(self):
        from analyzer.app.library import Library
        folder = self.home / "mains"
        folder.mkdir()
        lib = Library(folder)
        self.addCleanup(lib.solves.shutdown)
        self.assertIn("Pas de ranges 6-max", lib.spot_set("6max_bb_btn_srp")["error"])
        self.write_charts()
        state = lib.spot_set("6max_bb_btn_srp")
        self.assertEqual((state["total"], state["done"]), (24, 0))
        view = lib.explorer_state("spot:6max_bb_btn_srp:KsKd4c")
        self.assertEqual((view["meta"]["positions"], view["meta"]["pair"], view["meta"]["format"]),
                         (["BB", "BTN"], "BB contre BTN", "6-max"))
        with self.assertRaises(KeyError):
            lib.spot_set("6max_bb_utg_srp")

    def test_solve_all_six_max(self):
        """Le bouton « Résoudre tous les flops 6-max manquants » : toutes les séries couvertes par tes charts, un flop
        de chaque série à tour de rôle, les pots les plus rapides d'abord."""
        from analyzer.app.library import Library
        folder = self.home / "mains"
        folder.mkdir()
        lib = Library(folder)
        self.addCleanup(lib.solves.shutdown)
        state = lib.ring_spot_sets()
        self.assertEqual((state["total"], state["covered"], state["current"]), (0, 0, None))  # pas encore de charts
        self.write_charts()  # cinq lignes : SB contre BB (trois pots), BB contre BTN (SRP, pot 3bet)
        state = lib.ring_spot_sets()
        self.assertEqual((state["total"], state["done"], state["covered"], state["busy"]), (120, 0, 5, 0))
        self.assertEqual(state["seconds"], 24 * (2 * (4200 + 600) + 2 * (1500 + 150) + (120 + 45)))
        self.assertIn("Pas de ranges 6-max", next(f for f in state["families"] if f["family"] == "6max_bb_co_3bet")["error"])
        queued = []

        def queue(self, family, board, spot, view, series):
            queued.append((family, board, series))
            return view
        with mock.patch.object(Library, "_queue_flop", queue), \
                mock.patch.dict(os.environ, {"ANALYZER_SOLVER": str(FAKE_SOLVER)}):  # « installé »
            lib.ring_spot_sets(start=True)
        self.assertEqual(len(queued), 120)
        self.assertEqual([f for f, _, _ in queued[:5]], ["6max_sb_bb_4bet", "6max_sb_bb_3bet", "6max_bb_btn_3bet",
                                                         "6max_sb_bb_srp", "6max_bb_btn_srp"])
        first = studyspots.flop_set("6max_sb_bb_4bet")[:2]
        self.assertEqual({b for _, b, _ in queued[:5]}, {first[0]})  # le premier flop de chaque série…
        self.assertEqual(queued[5][1], first[1])  # … puis le deuxième
        self.assertTrue(all(series for _, _, series in queued))

    @unittest.skipIf(os.name == "nt", "faux solveur : script exécutable POSIX")
    def test_solve_all_then_stop(self):
        from analyzer.app.library import Library
        folder = self.home / "mains"
        folder.mkdir()
        self.write_charts()
        lib = Library(folder)
        self.addCleanup(lib.solves.shutdown)
        with mock.patch.dict(os.environ, {"ANALYZER_SOLVER": str(FAKE_SOLVER)}):
            state = lib.ring_spot_sets(start=True)
            self.assertEqual(state["busy"], 120)
            self.assertEqual(lib.ring_spot_sets(start=True)["busy"], 120)  # déjà en file : rien en double
            state = lib.ring_spot_cancel()
            deadline = time.time() + 30
            while state["busy"] and time.time() < deadline:
                time.sleep(0.1)
                state = lib.ring_spot_sets()
        self.assertEqual(state["busy"], 0)


if __name__ == "__main__":
    unittest.main()

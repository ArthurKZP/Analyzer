import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from analyzer import settings
from analyzer.errors import NotFound
from analyzer.parsers import parse_text
from analyzer.theory import custom_ranges, postflop, ring_ranges, studyspots

FIXTURES = Path(__file__).parent / "fixtures"
SITES = Path(__file__).parent / "sites"


def sample(hand_id):
    hands = parse_text((FIXTURES / "betclic_sample.txt").read_text(encoding="utf-8"))
    return next(h for h in hands if h.hand_id == hand_id)


class HomeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.home = Path(tmp.name)


class StoreTest(HomeTest):
    def test_check_and_order(self):
        self.assertEqual(custom_ranges.check("AA, AKs:0.5,72o"), {"AA": 1.0, "AKs": 0.5, "72o": 1.0})
        for bad in ("AKx", "KAs", "AAs", "", "AA:0"):
            with self.assertRaises(ValueError):
                custom_ranges.check(bad)
        order = custom_ranges.hand_order()
        self.assertEqual((len(order), len(set(order)), order[0], order[-1]), (169, 169, "AA", "32o"))
        self.assertTrue(all(custom_ranges.valid_class(h) for h in order))
        self.assertTrue(custom_ranges.same({"AA": 1.0, "KK": 0.5}, {"AA": 0.999, "KK": 0.5, "QQ": 0.001}))
        self.assertFalse(custom_ranges.same({"AA": 1.0}, {"AA": 1.0, "KK": 0.25}))

    def test_hand_over_line(self):
        self.assertEqual(custom_ranges.lookup("H1", "HU|BTN:raise BB:call"), (None, {}))
        custom_ranges.save("ligne", "HU|BTN:raise BB:call", {"BB": "AA,KK", "BTN": "QQ:0.5"})
        custom_ranges.save("coup", "H1", {"BB": "AKs"})
        scope, ranges = custom_ranges.lookup("H1", "HU|BTN:raise BB:call")
        self.assertEqual((scope, ranges), ("coup", {"BB": {"AKs": 1.0}, "BTN": {"QQ": 0.5}}))
        self.assertEqual(custom_ranges.lookup("H2", "HU|BTN:raise BB:call")[0], "ligne")
        self.assertTrue(custom_ranges.clear("coup", "H1"))
        self.assertFalse(custom_ranges.clear("coup", "H1"))
        self.assertEqual(custom_ranges.lookup("H1", "HU|BTN:raise BB:call")[0], "ligne")
        self.assertEqual(ring_ranges.available(), {})  # tes ranges ajustées ne sont pas une solution de format
        with self.assertRaises(ValueError):
            custom_ranges.save("tout", "H1", {"BB": "AA"})


class LinesTest(HomeTest):
    """Les lignes de chaque format et leurs ranges de référence (Paramètres › Ranges préflop)."""

    def charts(self):
        ring_ranges.save_solution("6-max", {"format": "6-max", "source": "Mes charts — test", "lines": {
            "CO:raise BB:call": {"pot_type": "SRP", "ranges": {"CO": "AA,KK,AQs", "BB": "QQ,AJs"}},
            "UTG:raise BB:call": {"ranges": {"UTG": "AA", "BB": "KK"}},
            "CO:raise BB:raise CO:call": {"ranges": {"CO": "KK", "BB": "AA"}},
            "BTN:raise SB:raise BTN:call": {"ranges": {"BTN": "QQ", "SB": "AA,KK"}},
            "CO:raise BTN:call BB:call": {"ranges": {"CO": "AA", "BTN": "KK", "BB": "QQ"}},  # à trois : pas réglable
            "illisible": {"ranges": {"CO": "AA"}}}})

    def test_heads_up(self):
        lines = custom_ranges.reference_lines("HU")
        self.assertEqual([(line["key"], line["pot_type"], list(line["ranges"])) for line in lines],
                         [("BTN:raise BB:call", "SRP", ["BTN", "BB"]),
                          ("BTN:raise BB:raise BTN:call", "pot 3bet", ["BTN", "BB"]),
                          ("BTN:raise BB:raise BTN:raise BB:call", "pot 4bet", ["BTN", "BB"])])
        spot = studyspots.parse_ident("spot:3bet:KsKd4c")  # les ranges des spots d'étude de la même ligne
        self.assertEqual(lines[1]["ranges"], {"BTN": spot.ranges["BTN"], "BB": spot.ranges["BB"]})
        self.assertEqual(custom_ranges.formats(), ["HU"])  # sans charts : le heads-up seulement
        self.assertEqual(custom_ranges.formats(["6-max"], heads_up=False), [])
        self.assertEqual(custom_ranges.reference_name("HU"), "solution préflop heads-up")
        self.assertIsNone(custom_ranges.reference_line("HU|BTN:call BB:check"))  # pot limpé : pas de référence
        self.assertEqual(custom_ranges.reference_line("HU|BTN:raise BB:call")["pot_type"], "SRP")

    def test_ring_formats(self):
        self.charts()
        self.assertEqual(custom_ranges.formats(["8 joueurs", "3-max", "4-max", "HU"]),
                         ["HU", "3-max", "6-max", "8 joueurs"])  # « 4-max » : un format que tes mains n'ont pas
        self.assertEqual(custom_ranges.formats(["8 joueurs"], heads_up=False, ring=True), ["6-max", "8 joueurs"])
        six = custom_ranges.reference_lines("6-max")
        self.assertEqual([line["key"] for line in six],  # pots simples d'abord, puis dans l'ordre de parole
                         ["UTG:raise BB:call", "CO:raise BB:call", "CO:raise BB:raise CO:call",
                          "BTN:raise SB:raise BTN:call"])
        self.assertEqual(six[1]["ranges"], {"CO": {"AA": 1.0, "KK": 1.0, "AQs": 1.0}, "BB": {"QQ": 1.0, "AJs": 1.0}})
        self.assertEqual(custom_ranges.reference_name("6-max"), "Mes charts (6-max)")
        # 8 joueurs : les charts 6-max à même nombre de joueurs derrière (le LJ ouvre comme l'UTG de 6-max)
        eight = custom_ranges.reference_lines("8 joueurs")
        self.assertEqual([line["key"] for line in eight][:2], ["LJ:raise BB:call", "CO:raise BB:call"])
        self.assertEqual(eight[0]["ranges"], {"LJ": {"AA": 1.0}, "BB": {"KK": 1.0}})
        self.assertEqual(custom_ranges.reference_name("8 joueurs"), "Mes charts (6-max), position pour position")
        # 3-max sans charts à lui : seules les lignes entre le bouton et les blindes
        self.assertEqual([line["key"] for line in custom_ranges.reference_lines("3-max")],
                         ["BTN:raise SB:raise BTN:call"])
        self.assertEqual(custom_ranges.reference_lines("4-max"), [])
        self.assertIsNone(custom_ranges.reference_line("6-max|HJ:raise BB:call"))
        self.assertEqual(custom_ranges.table_seats("7 joueurs"), ("UTG", "LJ", "HJ", "CO", "BTN", "SB", "BB"))

    def test_spots_option(self):
        self.assertFalse(custom_ranges.for_spots())
        self.assertTrue(custom_ranges.set_for_spots(True))
        custom_ranges.save("ligne", "HU|BTN:raise BB:call", {"BB": "AA"})  # l'option reste
        self.assertTrue(custom_ranges.for_spots())
        custom_ranges.clear("ligne", "HU|BTN:raise BB:call")
        self.assertTrue(custom_ranges.load()["spots"])
        self.assertFalse(custom_ranges.set_for_spots(False))


class SpotTest(HomeTest):
    def test_heads_up_hand(self):
        hand = sample("HAND01")  # SRP : Hero au bouton, Villain en BB
        reference = postflop.build_spot(hand, "Hero")
        self.assertEqual((reference.context, reference.adjusted), ("HU|BTN:raise BB:call", None))
        custom_ranges.save("ligne", reference.context, {"BB": "AA,KK,QQ"})
        spot = postflop.build_spot(hand, "Hero")
        self.assertEqual(spot.adjusted, "ligne")
        self.assertEqual(spot.ranges["Villain"], {"AA": 1.0, "KK": 1.0, "QQ": 1.0})
        self.assertEqual(spot.ranges["Hero"], reference.ranges["Hero"])  # le bouton garde la référence
        self.assertEqual(spot.reference, reference.ranges)
        self.assertNotEqual(postflop.study_key(spot.request()), postflop.study_key(reference.request()))
        same = postflop.build_spot(hand, "Hero", custom=False)  # la référence reste disponible
        self.assertEqual((same.adjusted, postflop.study_key(same.request())),
                         (None, postflop.study_key(reference.request())))
        custom_ranges.save("coup", hand.hand_id, {"BTN": "AA,AKs"})
        spot = postflop.build_spot(hand, "Hero")
        self.assertEqual(spot.adjusted, "coup")
        self.assertEqual(set(spot.ranges["Hero"]), {"AA", "AKs", "K9s"})  # sa main ajoutée avec un poids infime
        self.assertEqual(spot.ranges["Hero"]["K9s"], postflop.MIN_WEIGHT)
        self.assertEqual(spot.ranges["Villain"], {"AA": 1.0, "KK": 1.0, "QQ": 1.0})  # la ligne pour l'autre
        result = postflop.interpret(spot, {"decisions": [], "iterations": 1, "exploit_pct": 1.0, "seconds": 1.0,
                                           "tree_nodes": 1})
        self.assertEqual(result["adjusted"], "coup")
        self.assertIn("ajustées pour ce coup", postflop.result_text(result, "Hero"))

    def test_ring_hand(self):
        ring_ranges.save_solution("3-max", {"format": "3-max", "lines": {
            "BTN:raise BB:raise BTN:call": {"ranges": {"BTN": "AA,KK:0.5,AQs,A9s", "BB": "AA,KK,QQ,AKs"}}}})
        hand = parse_text((SITES / "winamax.txt").read_text(encoding="utf-8"))[0]
        spot = postflop.build_spot(hand, "Hero")
        self.assertEqual(spot.context, "3-max|BTN:raise BB:raise BTN:call")  # le fold de la SB ne compte pas
        custom_ranges.save("ligne", spot.context, {"BTN": "AA,KK"})
        spot = postflop.build_spot(hand, "Hero")
        self.assertEqual((spot.adjusted, spot.ranges[spot.ip]),  # sa main montrée ajoutée avec un poids infime
                         ("ligne", {"AA": 1.0, "KK": 1.0, "A9s": postflop.MIN_WEIGHT}))
        self.assertEqual(spot.reference[spot.ip], {"AA": 1.0, "KK": 0.5, "AQs": 1.0, "A9s": 1.0})

    def test_study_spot(self):
        ident = "spot:srp:KsKd4c"
        reference = studyspots.parse_ident(ident)
        self.assertEqual(reference.context, "HU|BTN:raise BB:call")
        custom_ranges.save("ligne", "HU|BTN:raise BB:call", {"BB": "AA"})  # pas pour les spots d'étude
        custom_ranges.save("coup", ident, {"BB": "AA,KK"})
        self.assertIsNone(studyspots.parse_ident(ident).adjusted)  # séries, plans et références : la théorie
        spot = studyspots.parse_ident(ident, custom=True)
        self.assertEqual((spot.adjusted, spot.ranges["BB"]), ("coup", {"AA": 1.0, "KK": 1.0}))
        self.assertEqual(spot.reference, reference.ranges)
        self.assertEqual(spot.interpret({})["adjusted"], "coup")
        request = spot.request()
        study = postflop.study_path(request)
        study.parent.mkdir(parents=True, exist_ok=True)
        study.write_bytes(b"x")
        spot.write_meta(request, {"iterations": 10, "exploit_pct": 0.5, "seconds": 3})
        meta = postflop.study_meta(request)
        self.assertEqual((meta["kind"], meta["hand"], meta["adjusted"]), ("spot-ajuste", ident, "coup"))
        self.assertEqual(studyspots.spot_studies(), {})  # ni dans la série…
        self.assertEqual(studyspots.stale_spot_studies(), [])  # …ni parmi les études à refaire
        from analyzer.app.studies import build_studies_page
        page = build_studies_page(section="coups")
        self.assertIn("tes ranges", page)
        self.assertIn("/explorateur/spot%3Asrp%3AKsKd4c", page)
        with mock.patch("analyzer.theory.coach.extract_and_save") as extract:
            spot.after_solve(None)  # pas de plan de jeu tiré de tes ranges
            extract.assert_not_called()

    def test_study_spot_with_line_defaults(self):
        ident = "spot:srp:KsKd4c"
        custom_ranges.save("ligne", "HU|BTN:raise BB:call", {"BB": "AA", "BTN": "KK"})
        custom_ranges.set_for_spots(True)  # option des Paramètres : la ligne vaut aussi pour les spots d'étude
        self.assertIsNone(studyspots.parse_ident(ident).adjusted)  # séries, plans et références : toujours la théorie
        spot = studyspots.parse_ident(ident, custom=True)
        self.assertEqual((spot.adjusted, spot.ranges["BB"], spot.ranges["BTN"]), ("ligne", {"AA": 1.0}, {"KK": 1.0}))
        custom_ranges.save("coup", ident, {"BB": "QQ"})  # le réglage du spot passe avant
        spot = studyspots.parse_ident(ident, custom=True)
        self.assertEqual((spot.adjusted, spot.ranges["BB"], spot.ranges["BTN"]), ("coup", {"QQ": 1.0}, {"KK": 1.0}))
        custom_ranges.set_for_spots(False)
        self.assertEqual(studyspots.parse_ident(ident, custom=True).ranges["BTN"], spot.reference["BTN"])


class LiveSessionTest(unittest.TestCase):
    def test_live_session_matches_ranges(self):
        from analyzer.app.solves import SolveQueue
        queue = SolveQueue()
        self.addCleanup(queue.shutdown)
        session = SimpleNamespace(request={"spot": 1}, alive=True, close=lambda: None, last_used=0)
        queue._set_live("H1", session)
        self.assertIs(queue.live_session("H1"), session)
        self.assertIs(queue.live_session("H1", {"spot": 1}), session)
        self.assertIsNone(queue.live_session("H1", {"spot": 2}))  # autres ranges : une autre étude
        self.assertIsNone(queue.live_session("H2"))


class LibraryTest(HomeTest):
    def setUp(self):
        super().setUp()
        folder = self.home / "mains"
        folder.mkdir()
        shutil.copy(FIXTURES / "betclic_sample.txt", folder / "sample.txt")
        from analyzer.app.library import Library
        self.lib = Library(folder)
        self.addCleanup(self.lib.solves.shutdown)

    def test_state_save_clear(self):
        lib = self.lib
        state = lib.ranges_state("HAND01")
        self.assertEqual((state["supported"], state["line"], state["format"], state["can_line"], state["adjusted"]),
                         (True, "BTN open, BB call", "HU", True, None))
        self.assertEqual([(p["position"], p["role"], p["source"]) for p in state["players"]],
                         [("BB", "V", "reference"), ("BTN", "H", "reference")])
        self.assertEqual(len(state["order"]), 169)
        bb_ref = postflop.range_text(state["players"][0]["reference"])
        btn_ref = postflop.range_text(state["players"][1]["reference"])

        state = lib.save_ranges("HAND01", "coup", {"BB": "AA,KK", "BTN": btn_ref})
        self.assertEqual(state["adjusted"], "coup")
        self.assertEqual([p["source"] for p in state["players"]], ["coup", "reference"])  # BTN inchangé : pas gardé
        self.assertEqual(custom_ranges.load()["coups"], {"HAND01": {"BB": "AA,KK"}})
        self.assertEqual(lib.solve("HAND01")["adjusted"], "coup")

        state = lib.save_ranges("HAND01", "ligne", {"BB": "AA,KK,QQ", "BTN": btn_ref})
        self.assertEqual([p["source"] for p in state["players"]], ["ligne", "reference"])
        self.assertEqual(custom_ranges.load()["coups"], {})  # la ligne remplace le réglage du coup
        # pour ce coup seulement, la range de la référence face à ta ligne : gardée
        state = lib.save_ranges("HAND01", "coup", {"BB": bb_ref, "BTN": btn_ref})
        self.assertEqual([p["source"] for p in state["players"]], ["coup", "reference"])
        self.assertEqual(state["players"][0]["line"], {"AA": 1.0, "KK": 1.0, "QQ": 1.0})

        state = lib.clear_ranges("HAND01", "coup")
        self.assertEqual(state["adjusted"], "ligne")
        self.assertEqual(lib.ranges_state("HAND02")["adjusted"], None)  # autre ligne (pot 3bet)
        state = lib.save_ranges("HAND01", "ligne", {"BB": bb_ref, "BTN": btn_ref})  # rien ne diffère : effacé
        self.assertEqual((state["adjusted"], custom_ranges.load()["lignes"]), (None, {}))

        for scope, ranges in (("coup", {"SB": "AA"}), ("coup", {}), ("tout", {"BB": "AA"}), ("coup", {"BB": "AXs"}),
                              ("coup", {"BB": ""})):
            with self.assertRaises(ValueError):
                lib.save_ranges("HAND01", scope, ranges)
        self.assertFalse(lib.ranges_state("HAND03")["supported"])  # tapis préflop

    def test_default_ranges(self):
        lib = self.lib
        view = lib.default_ranges_view()
        self.assertEqual([(g["format"], g["hands"], len(g["lines"])) for g in view["formats"]], [("HU", 4, 3)])
        self.assertEqual((view["orphans"], view["for_spots"], view["charts"]), ([], False, False))
        first = view["formats"][0]["lines"][0]
        self.assertEqual(first, {"context": "HU|BTN:raise BB:call", "line": "BTN open, BB call", "pot_type": "SRP",
                                 "positions": ["BTN", "BB"], "adjusted": []})
        line = lib.default_range_line("HU|BTN:raise BB:call")
        self.assertEqual((line["line"], line["format"], line["adjusted"], len(line["order"])),
                         ("BTN open, BB call", "HU", None, 169))
        self.assertEqual([(p["position"], p["source"]) for p in line["players"]], [("BTN", "reference"), ("BB", "reference")])
        btn_ref = postflop.range_text(line["players"][0]["reference"])

        line = lib.save_default_ranges("HU|BTN:raise BB:call", {"BTN": btn_ref, "BB": "AA,KK"})
        self.assertEqual([p["source"] for p in line["players"]], ["reference", "ligne"])  # BTN inchangé : pas gardé
        self.assertEqual(custom_ranges.load()["lignes"], {"HU|BTN:raise BB:call": {"BB": "AA,KK"}})
        self.assertEqual(lib.ranges_state("HAND01")["adjusted"], "ligne")  # tes coups de la ligne la prennent
        self.assertEqual(lib.solve("HAND01")["adjusted"], "ligne")
        lib.save_default_ranges("HU|BTN:raise BB:call", {"BTN": "AA,AKs"})  # BB garde son réglage
        self.assertEqual(custom_ranges.load()["lignes"]["HU|BTN:raise BB:call"], {"BB": "AA,KK", "BTN": "AA,AKs"})
        self.assertEqual(lib.default_ranges_view()["formats"][0]["lines"][0]["adjusted"], ["BTN", "BB"])
        lib.save_default_ranges("HU|BTN:raise BB:call", {"BTN": btn_ref})
        self.assertEqual(custom_ranges.load()["lignes"]["HU|BTN:raise BB:call"], {"BB": "AA,KK"})

        for context, ranges in (("HU|BTN:raise BB:call", {"SB": "AA"}), ("HU|BTN:raise BB:call", {}),
                                ("HU|BTN:raise BB:call", {"BB": "AXs"}), ("HU|BTN:raise BB:call", "AA")):
            with self.assertRaises(ValueError):
                lib.save_default_ranges(context, ranges)
        with self.assertRaises(NotFound):
            lib.save_default_ranges("HU|BTN:call BB:check", {"BB": "AA"})
        with self.assertRaises(NotFound):
            lib.default_range_line("6-max|CO:raise BB:call")  # sans charts 6-max

        custom_ranges.save("ligne", "6-max|CO:raise BB:call", {"BB": "AA"})  # une ligne sans référence
        view = lib.default_ranges_view()
        self.assertEqual(view["orphans"], [{"context": "6-max|CO:raise BB:call", "format": "6-max",
                                            "line": "CO open, BB call", "positions": ["BB"]}])
        self.assertEqual(lib.clear_default_ranges("6-max|CO:raise BB:call"), {"context": "6-max|CO:raise BB:call",
                                                                             "cleared": True})
        self.assertEqual(lib.clear_default_ranges("HU|BTN:raise BB:call")["cleared"], True)
        self.assertEqual((custom_ranges.load()["lignes"], lib.ranges_state("HAND01")["adjusted"]), ({}, None))
        settings.save({"formats": ["ring"]})  # le heads-up que tu ne joues pas : pas listé
        self.assertEqual(lib.default_ranges_view()["formats"], [])

    def test_study_spot_with_option(self):
        ident = "spot:srp:KsKd4c"
        self.assertEqual(self.lib.set_ranges_for_spots(True), {"for_spots": True})
        state = self.lib.ranges_state(ident)
        self.assertEqual((state["can_line"], state["for_spots"]), (True, True))
        state = self.lib.save_ranges(ident, "ligne", {"BB": "AA,KK"})
        self.assertEqual((state["adjusted"], [p["source"] for p in state["players"]]), ("ligne", ["ligne", "reference"]))
        self.assertEqual(custom_ranges.load()["lignes"], {"HU|BTN:raise BB:call": {"BB": "AA,KK"}})
        self.assertEqual(self.lib.ranges_state("HAND01")["adjusted"], "ligne")  # la même ligne que tes coups
        self.lib.set_ranges_for_spots(False)
        self.assertEqual(self.lib.ranges_state(ident)["adjusted"], None)

    def test_study_spot(self):
        state = self.lib.ranges_state("spot:3bet:KsKd4c")
        self.assertEqual((state["spot"], state["can_line"], state["line"]), (True, False, "BTN open, BB 3bet, BTN call"))
        self.assertEqual([p["position"] for p in state["players"]], ["BB", "BTN"])
        with self.assertRaises(ValueError):
            self.lib.save_ranges("spot:3bet:KsKd4c", "ligne", {"BB": "AA"})
        state = self.lib.save_ranges("spot:3bet:KsKd4c", "coup", {"BTN": "AA,KK,AKs"})
        self.assertEqual(state["adjusted"], "coup")
        self.assertEqual(self.lib._spot("spot:3bet:KsKd4c").ranges["BTN"], {"AA": 1.0, "KK": 1.0, "AKs": 1.0})


if __name__ == "__main__":
    unittest.main()

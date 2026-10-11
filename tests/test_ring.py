import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer import ring
from analyzer.app.ring_page import build_ring_page
from analyzer.parsers import load_hands, parse_text
from analyzer.theory import postflop, ring_ranges

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

SITES = Path(__file__).parent / "sites"
# les exemples Betclic, Unibet et Winamax (les autres sites ont leurs tests dans test_sites)
FIRST_SITES = [SITES / name for name in ("betclic_6max.txt", "unibet.txt", "winamax.txt")]


def hand(name, index=0):
    return parse_text((SITES / name).read_text(encoding="utf-8"))[index]


class ReadTest(unittest.TestCase):
    def test_six_max_betclic(self):
        h = hand("betclic_6max.txt")
        # Hero au HJ, premier à parler après le fold d'UTG : il folde
        self.assertEqual(ring.read(h, "Hero"), {"open": False, "limp": False, "vpip": False, "pfr": False})
        # la BB 3bet le vol du CO, checke le flop (pas de c-bet), gagne à l'abattage
        bb = ring.read(h, "Joueur6")
        self.assertEqual((bb["fold_steal"], bb["threebet_steal"], bb["threebet"], bb["cbet_hu"]), (False, True, True, False))
        self.assertEqual((bb["wtsd"], bb["wsd"]), (True, True))
        # le CO ouvre, paie le 3bet, ne folde pas face à la c-bet (il n'y en a pas)
        co = ring.read(h, "Joueur3")
        self.assertEqual((co["open"], co["fold_3bet"], co["wtsd"], co["wsd"]), (True, False, True, False))
        self.assertNotIn("fold_cbet", co)

    def test_three_max_winamax(self):
        h = hand("winamax.txt")
        me = ring.read(h, "Hero")  # en BB contre l'open du bouton : 3bet, c-bet, perdu à l'abattage
        self.assertEqual((me["fold_steal"], me["threebet_steal"], me["cbet_hu"], me["wtsd"], me["wsd"]),
                         (False, True, True, True, False))
        btn = ring.read(h, "Incognito-a1b2c3d4")
        self.assertEqual((btn["open"], btn["fold_3bet"], btn["fold_cbet"]), (True, False, False))

    def test_limp_then_raise_is_not_a_steal(self):
        me = ring.read(hand("unibet.txt"), "Hero")  # le HJ limpe, la SB relance : pas un vol
        self.assertEqual((me["flat"], me["threebet"], me["wtsd"], me["wsd"]), (True, False, True, True))
        self.assertNotIn("fold_steal", me)


class AnalyzeTest(unittest.TestCase):
    def test_formats_positions_and_page(self):
        hands = load_hands(FIRST_SITES)
        stats = ring.analyze(hands, "Hero")
        self.assertEqual([fs.table_format for fs in stats], ["6-max", "3-max"])  # le HU n'en fait pas partie
        six = stats[0]
        self.assertEqual((six.total.hands, [p.position for p in six.positions]), (2, ["HJ", "BB"]))
        self.assertEqual(six.sites, ["Betclic", "Unibet"])
        self.assertAlmostEqual(six.total.net_bb, 88.5 / 5)
        self.assertEqual(ring.ref("6-max", "open", "BTN"), (40, 50))
        self.assertIsNone(ring.ref("3-max", "vpip"))
        # toutes les tables ensemble : une position compte ses mains de chaque taille de table
        merged = ring.analyze(hands, "Hero", merge=True)
        self.assertEqual([(fs.table_format, fs.total.hands, fs.formats) for fs in merged],
                         [(ring.MERGED, 3, ["3-max", "6-max"])])
        self.assertEqual([p.position for p in merged[0].positions], ["HJ", "BB"])
        self.assertEqual(ring.ref(ring.MERGED, "open", "HJ"), (18, 23))
        scopes = [("all", "Toutes", merged), ("reg", "Réguliers", merged), ("rec", "Récréatifs", [])]
        page = build_ring_page(scopes, "Hero")
        for text in ("Préflop par position", "Après le flop", "Toutes tes mains (3)", "Contre les réguliers (3)",
                     "repère 18–23&nbsp;%", "3-max, 6-max"):
            self.assertIn(text, page)
        self.assertNotIn("Contre les récréatifs (", page)  # aucune main contre eux : pas de bouton
        self.assertIn("Aucune main", build_ring_page([], "Hero"))
        # un pot à deux au flop sans ranges : de quoi charger les charts de Hand2Note Guide
        row = {"id": "X1", "date": stats[0].first, "format": "6-max", "hero": "CO", "villain": "BB",
               "line": "CO open, BB call", "pot_type": None, "cards": ["Ah", "Kd"], "board": ["2c", "7d", "9s"],
               "total_bb": 12.0, "net_bb": 5.5, "status": "Pas de range préflop pour « CO open, BB call » en 6-max"}
        page = build_ring_page(scopes, "Hero", spots=[row], ranges={})
        self.assertIn("Charger les charts 100 bb de Hand2Note Guide (6-max et 3-max)", page)
        self.assertIn("pas de range préflop", page)
        ready = build_ring_page(scopes, "Hero", spots=[dict(row, status=None, pot_type="SRP")], ranges={"6-max": 23})
        self.assertNotIn("Charger les charts", ready)
        self.assertIn('href="/explorateur/X1"', ready)


class HeadsUpPotTest(unittest.TestCase):
    """Pots à deux joueurs au flop à une table à plusieurs : le solveur postflop, avec les ranges de ta solution."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.home = Path(tmp.name)
        self.hand = hand("winamax.txt")  # 3-max : toi en BB, 3bet contre l'open du bouton, la SB folde

    def write_ranges(self):
        ring_ranges.save_solution("3-max", {"format": "3-max", "lines": {
            "BTN:raise BB:raise BTN:call": {"ranges": {"BTN": "AA,KK:0.5,AQs,A9s", "BB": "AA,KK,QQ,AKs"}}}})

    def test_line_and_missing_ranges(self):
        self.assertEqual(postflop.flop_pair(self.hand), ("Hero", "Incognito-a1b2c3d4"))
        steps = [("BTN", "raise"), ("BB", "raise"), ("BTN", "call")]
        self.assertEqual((ring_ranges.line_key(steps), ring_ranges.describe(steps)),
                         ("BTN:raise BB:raise BTN:call", "BTN open, BB 3bet, BTN call"))
        with self.assertRaises(postflop.Unsupported) as err:
            postflop.build_spot(self.hand, "Hero")
        self.assertIn("Pas de range préflop pour « BTN open, BB 3bet, BTN call » en 3-max", str(err.exception))
        multiway = hand("unibet.txt")  # trois joueurs au flop
        self.assertIsNone(postflop.flop_pair(multiway))
        with self.assertRaises(postflop.Unsupported) as err:
            postflop.build_spot(multiway, "Hero")
        self.assertIn("Pot à 3 joueurs au flop", str(err.exception))

    def test_spot_with_ranges(self):
        self.write_ranges()
        self.assertEqual(ring_ranges.available(), {"3-max": 1})
        spot = postflop.build_spot(self.hand, "Hero")
        self.assertEqual((spot.oop, spot.ip, spot.pot_type), ("Hero", "Incognito-a1b2c3d4", "pot 3bet"))
        self.assertEqual((spot.pot_bb, spot.stack_bb), (25.5, 94.125))  # la SB morte compte dans le pot
        self.assertEqual(spot.ranges["Incognito-a1b2c3d4"], {"AA": 1.0, "KK": 0.5, "AQs": 1.0, "A9s": 1.0})
        self.assertEqual(spot.ranges["Hero"]["KK"], 1.0)
        self.assertEqual([s.get("action") or s.get("card") for s in spot.line][:2], ["bet", "call"])
        request = spot.request()
        self.assertEqual(request["spot"]["tree"]["starting_pot"], 25.5)
        steps = postflop.preflop_steps(self.hand, "Hero")
        self.assertEqual([(s["position"], s["player"], s["name"]) for s in steps],
                         [("BTN", 1, "Open 2,5"), ("SB", None, "Fold"), ("BB", 0, "3bet 12,5"), ("BTN", 1, "Call")])
        self.assertIsNone(steps[0]["line"])  # pas d'arbre préflop HU à ouvrir

    def test_library(self):
        self.write_ranges()
        folder = self.home / "mains"
        folder.mkdir()
        for name in ("winamax.txt", "unibet.txt", "betclic_6max.txt"):
            (folder / name).write_text((SITES / name).read_text(encoding="utf-8"), encoding="utf-8")
        from analyzer.app.library import Library
        lib = Library(folder)
        try:
            rows = lib.ring_spots()
            self.assertEqual([(r["format"], r["hero"], r["villain"], r["status"]) for r in rows],
                             [("3-max", "BB", "BTN", None)])
            self.assertIsNotNone(rows[0]["pot_type"])
            unprepared = lib.ring_spots(0)  # au-delà de la limite : pas préparés pour le solveur (la page n'en montre pas)
            self.assertEqual([(r["id"], r["status"], r["pot_type"]) for r in unprepared], [(rows[0]["id"], None, None)])
            state = lib.explorer_state(rows[0]["id"])
            meta = state["meta"]
            self.assertEqual((meta["positions"], meta["hero_oop"], meta["table_format"]), (["BB", "BTN"], True, "3-max"))
            self.assertEqual(state["state"], "absent")  # prêt à résoudre
            page = lib.self_page("tables")
            self.assertIn("Ouvrir au solveur", page)
            self.assertIn("BTN open, BB 3bet, BTN call", page)
        finally:
            lib.solves.shutdown()


# Extrait synthétique au format du fichier de données des charts de Hand2Note Guide (pas leurs ranges).
H2N_SAMPLE = """
var GTO_RANGES = (function () {
    function R(v)      { return {R: v}; }
    function C(v)      { return {C: v}; }
    function RC(r, c)  { return {R: r, C: c}; }
    var rfi = {};
    rfi.CO = { "AA":R(100),"KK":R(100),"AQs":R(100),"KJs":R(50),"76s":R(25) };
    var vs_rfi = {};
    vs_rfi.BB_vs_CO = { "AA":R(100),"KK":RC(75,25),"AQs":C(100),"KJs":{R:25, C:75},"T9s":C(50) };
    var vs_3bet = {};
    var vs_3bet_base = {};
    vs_3bet_base.CO = { "AA":R(100),"KK":RC(50,50),"AQs":C(100),"KJs":C(50) };
    function assignVs3betDefaults(hero, villains) {}
    assignVs3betDefaults('CO',  ['BTN', 'SB', 'BB']);
    vs_3bet.CO_vs_BTN = { "AA": {R:50, B:50}, "KK": {C:100} };
    return { rfi: rfi, vs_rfi: vs_rfi, vs_3bet: vs_3bet };
})();
"""


class Hand2NoteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_charts_to_lines(self):
        charts = ring_ranges.parse_hand2note(H2N_SAMPLE)
        self.assertEqual(charts["rfi"]["CO"]["KJs"], {"R": 0.5})
        self.assertEqual(charts["vs_rfi"]["BB_vs_CO"]["KK"], {"R": 0.75, "C": 0.25})
        self.assertEqual(charts["vs_3bet"]["CO_vs_BB"]["KK"], {"R": 0.5, "C": 0.5})  # range de base de l'ouvreur
        self.assertEqual(charts["vs_3bet"]["CO_vs_BTN"]["AA"], {"R": 0.5, "B": 0.5})  # range par adversaire
        lines = ring_ranges.lines_from_charts(charts)
        self.assertEqual(lines["CO:raise BB:call"]["ranges"],
                         {"CO": "AA,KK,AQs,KJs:0.5,76s:0.25", "BB": "KK:0.25,AQs,KJs:0.75,T9s:0.5"})
        # pot 3bet : l'ouvreur garde ce qu'il ouvre ET paie le 3bet ; le 3bettor, sa range de 3bet
        self.assertEqual(lines["CO:raise BB:raise CO:call"]["ranges"],
                         {"CO": "KK:0.5,AQs,KJs:0.25", "BB": "AA,KK:0.75,KJs:0.25"})
        self.assertNotIn("CO:raise BB:raise CO:raise BB:call", lines)  # sans réponse au 4bet
        # pot 4bet : le 4bet hors tapis de l'ouvreur, payé par le 3bettor selon la réponse type
        four = ring_ranges.lines_from_charts(charts, vs4bet={"KK": {"C": 0.5}, "KJs": {"C": 1.0}})
        self.assertEqual(four["CO:raise BB:raise CO:raise BB:call"],
                         {"pot_type": "pot 4bet", "ranges": {"CO": "AA,KK:0.5", "BB": "KK:0.375,KJs:0.25"}})
        reference = ring_ranges.vs4bet_reference()  # la capture mesurée : AQs paie, AKs part à tapis
        self.assertEqual((reference["AQs"], "AKs" in reference), ({"C": 1.0}, False))

    def test_import_a_solution_file(self):
        path = self.home / "ma-solution.json"
        path.write_text('{"format": "3-max", "lines": {"BTN:raise BB:call": {"ranges": {"BTN": "AA", "BB": "KK"}}}}',
                        encoding="utf-8")
        with mock.patch("sys.stdout"):
            self.assertEqual(ring_ranges.main(["--importer", str(path)]), 0)
        self.assertEqual(ring_ranges.available(), {"3-max": 1})
        path.write_text('{"lines": {}}', encoding="utf-8")
        self.assertEqual(ring_ranges.import_file(path, "6-max"), "6-max")  # format donné à part
        with self.assertRaises(ValueError):
            ring_ranges.import_file(path)  # ni dans le fichier, ni dans son nom
        path.write_text('["pas une solution"]', encoding="utf-8")
        with self.assertRaises(ValueError):
            ring_ranges.import_file(path, "6-max")

    def test_install_and_solve_a_six_max_pot(self):
        formats = ring_ranges.install_hand2note(log=lambda message: None, js=H2N_SAMPLE)
        self.assertEqual(formats, ["6-max"])  # pas de chart BTN/SB/BB : pas de 3-max
        self.assertEqual(ring_ranges.available(), {"6-max": 3})  # pot simple, pot 3bet, pot 4bet
        six = hand("betclic_6max.txt")  # le CO ouvre, la BB 3bet, le CO paie
        spot = postflop.build_spot(six, "Joueur6")
        self.assertEqual((spot.oop, spot.ip, spot.pot_type), ("Joueur6", "Joueur3", "pot 3bet"))
        self.assertEqual(spot.ranges["Joueur3"]["KK"], 0.5)
        self.assertEqual(spot.pot_bb, 15.0)  # 14,5 + 14,5 + la SB morte (1) = 30 € à 2 € la bb


if __name__ == "__main__":
    unittest.main()

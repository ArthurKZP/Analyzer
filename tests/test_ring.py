import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer import ring
from analyzer.app.ring_page import build_ring_page
from analyzer.parsers import load_hands, parse_text
from analyzer.theory import postflop, ring_ranges

SITES = Path(__file__).parent / "sites"


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
        hands = load_hands([SITES])
        stats = ring.analyze(hands, "Hero")
        self.assertEqual([fs.table_format for fs in stats], ["6-max", "3-max"])  # le HU n'en fait pas partie
        six = stats[0]
        self.assertEqual((six.total.hands, [p.position for p in six.positions]), (2, ["HJ", "BB"]))
        self.assertEqual(six.sites, ["Betclic", "Unibet"])
        self.assertAlmostEqual(six.total.net_bb, 88.5 / 5)
        self.assertEqual(ring.ref("6-max", "open", "BTN"), (40, 50))
        self.assertIsNone(ring.ref("3-max", "vpip"))
        page = build_ring_page(stats, "Hero")
        for text in ("Préflop par position", "Après le flop", "6-max (2)", "3-max (1)", "repère 18–23&nbsp;%"):
            self.assertIn(text, page)
        self.assertIn("Aucune main", build_ring_page([], "Hero"))


class HeadsUpPotTest(unittest.TestCase):
    """Pots à deux joueurs au flop à une table à plusieurs : le solveur postflop, avec les ranges de ta solution."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"ANALYZER_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.home = Path(tmp.name)
        self.hand = hand("winamax.txt")  # 3-max : toi en BB, 3bet contre l'open du bouton, la SB folde

    def write_ranges(self):
        folder = ring_ranges.folder()
        folder.mkdir(parents=True)
        (folder / "3-max.json").write_text(json.dumps({"format": "3-max", "lines": {
            "BTN:raise BB:raise BTN:call": {"ranges": {"BTN": "AA,KK:0.5,AQs,A9s", "BB": "AA,KK,QQ,AKs"}}}}),
            encoding="utf-8")

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
            state = lib.explorer_state(rows[0]["id"])
            meta = state["meta"]
            self.assertEqual((meta["positions"], meta["hero_oop"], meta["table_format"]), (["BB", "BTN"], True, "3-max"))
            self.assertEqual(state["state"], "absent")  # prêt à résoudre
            page = lib.self_page("tables")
            self.assertIn("Ouvrir au solveur", page)
            self.assertIn("BTN open, BB 3bet, BTN call", page)
        finally:
            lib.solves.shutdown()


if __name__ == "__main__":
    unittest.main()

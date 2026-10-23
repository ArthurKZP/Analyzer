"""Le catalogue des stats de l'onglet Statistiques (analyzer/statlib.py) : les mêmes chiffres que le reste de
l'application pour les stats qui existaient, les nouvelles lues main par main, ta sélection dans les paramètres et
la page."""
import shutil
import unittest
from pathlib import Path

from analyzer import field, ring, settings, statlib
from analyzer.insights import STAT_DEFS
from analyzer.models import BET, CALL, CHECK, FOLD, RAISE
from analyzer.parsers import load_hands
from analyzer.stats import analyze
from tests.base import IsolatedHome
from tests.test_ring_leaks import FOLDS_TO, co_vs_bb, table_hand

FIXTURES = Path(__file__).parent / "fixtures"
SITES = Path(__file__).parent / "sites"


def ratio(hands, sid, fmt="ring", hero="Hero") -> tuple:
    found = statlib.value(statlib.BY_ID[sid], fmt, statlib.tally(hands, hero, fmt))
    return (found.ratio.hits, found.ratio.opps) if found.value is not None and found.ratio else (None, found.sample)


class CatalogTest(unittest.TestCase):
    def test_catalog(self):
        ids = [s.id for s in statlib.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 100)
        for stat in statlib.CATALOG:
            self.assertIn(stat.group, statlib.GROUPS)
            self.assertIn(stat.kind, ("pct", "avg", "af", "afq", "result", "hands"))
            self.assertTrue(stat.help and stat.formats(), stat.id)
        for fmt, chosen in statlib.DEFAULTS.items():
            self.assertTrue(all(fmt in statlib.BY_ID[i].formats() for i in chosen), fmt)
        hu = {s.id for s in statlib.available("HU")}
        self.assertNotIn("squeeze", hu)  # pas de squeeze à deux
        self.assertNotIn("vpip_utg", hu)
        self.assertIn("vpip_btn", hu)
        self.assertIn("squeeze", {s.id for s in statlib.available("ring")})
        # les repères du reste de l'application
        self.assertEqual(statlib.BY_ID["rfi_co"].refs["ring"], ring.OPEN_6MAX["CO"])
        self.assertEqual(statlib.BY_ID["fold_3bet"].refs["HU"], STAT_DEFS["sb_vs_3bet.fold"].ref)
        self.assertEqual(statlib.BY_ID["fold_3bet"].refs["ring"], ring.REF_6MAX["fold_3bet"])
        self.assertEqual(statlib.BY_ID["cbet_t"].refs["ring"], field.RING_DEF["cbet_turn"].ref)

    def test_selection(self):
        self.assertEqual([s.id for s in statlib.selection("ring")], statlib.DEFAULTS["ring"])
        chosen = {"ring": ["squeeze", "vpip"]}
        self.assertEqual([s.id for s in statlib.selection("ring", chosen)], ["squeeze", "vpip"])
        self.assertEqual([s.id for s in statlib.selection("HU", chosen)], statlib.DEFAULTS["HU"])
        self.assertEqual(settings.check({"stat_selection": chosen}), {"stat_selection": chosen})
        self.assertEqual(settings.check({"stat_selection": None}), {"stat_selection": None})
        for wrong in ({"HU": ["squeeze"]}, {"ring": ["vpip", "vpip"]}, {"ring": ["inconnue"]}, {"6-max": ["vpip"]},
                      {"ring": "vpip"}, ["vpip"]):
            with self.assertRaises(ValueError, msg=wrong):
                settings.check({"stat_selection": wrong})


class SameNumbersTest(IsolatedHome):
    """Les stats qui existaient : les mêmes chiffres que le bilan, le Leakfinding et l'Étude du field."""

    def test_heads_up(self):
        hands = load_hands([FIXTURES / "betclic_sample.txt"])
        table = statlib.tally(hands, "Hero", "HU")
        ps = analyze(hands)["Hero"]
        checked = 0
        for stat in statlib.available("HU"):
            src = stat.src["HU"]
            if stat.kind == "pct" and not stat.positions and src in ps.ratios:
                found = statlib.value(stat, "HU", table).ratio
                self.assertEqual((found.hits, found.opps), (ps.r(src).hits, ps.r(src).opps), stat.id)
                checked += 1
        self.assertGreater(checked, 10)
        found = statlib.value(statlib.BY_ID["vpip_btn"], "HU", table).ratio
        self.assertEqual((found.hits, found.opps), (ps.r("vpip_sb").hits, ps.r("vpip_sb").opps))
        self.assertAlmostEqual(statlib.value(statlib.BY_ID["bb100"], "HU", table).value, 100 * ps.net_bb / ps.hands)
        self.assertEqual(statlib.value(statlib.BY_ID["hands"], "HU", table).value, ps.hands)

    def test_tables(self):
        hands = [h for h in load_hands([SITES / "betclic_6max.txt", SITES / "winamax.txt"]) if h.size > 2]
        hero = "Hero"
        hands = [h for h in hands if hero in h.seats] + [
            co_vs_bb(1, "CO", ["Ah", "Kd"], [("flop", "BB", CHECK), ("flop", "CO", BET, 6.0), ("flop", "BB", FOLD)]),
            co_vs_bb(2, "BB", ["Ah", "Kd"], [("flop", "BB", CHECK), ("flop", "CO", BET, 6.0), ("flop", "BB", CALL),
                                             ("turn", "BB", CHECK), ("turn", "CO", BET, 12.0), ("turn", "BB", FOLD)],
                     winner="CO")]
        table = statlib.tally(hands, hero, "ring")
        fs = ring.analyze(hands, hero, merge=True)[0]
        old = field.ring_ratios(hands, [hero])
        for stat in statlib.available("ring"):
            src = stat.src["ring"]
            if stat.kind != "pct" or stat.positions or "+" in src:
                continue
            want = fs.total.ratios.get(src) or old.get(src)
            if want is not None:
                found = statlib.value(stat, "ring", table).ratio
                self.assertEqual((found.hits, found.opps), (want.hits, want.opps), stat.id)
        bb = statlib.value(statlib.BY_ID["fold_steal_bb"], "ring", table, ("BB",)).ratio
        self.assertEqual(bb.opps, next(p for p in fs.positions if p.position == "BB").ratios["fold_steal"].opps)


class PopulationTest(IsolatedHome):
    """Un groupe de joueurs (l'Étude du field) : les mêmes chiffres que ses autres pages."""

    def test_tables(self):
        hands = [h for h in load_hands([SITES / "betclic_6max.txt", SITES / "winamax.txt"]) if h.size > 2]
        names = {p for h in hands for p in h.seats} - {"Hero"}
        table = statlib.tally_group(hands, names, "ring")
        old = field.ring_ratios(hands, names)
        for sid, key in (("vpip", "vpip"), ("fold_cbet_f", "fold_cbet"), ("afq", "afq"), ("fold_3bet", "fold_3bet")):
            found = statlib.value(statlib.BY_ID[sid], "ring", table).ratio
            self.assertEqual((found.hits, found.opps), (old[key].hits, old[key].opps), sid)
        self.assertEqual(table.total.hands, sum(len(names & h.seats.keys()) for h in hands))

    def test_heads_up(self):
        hands = load_hands([FIXTURES / "betclic_sample.txt"])
        names = {p for h in hands for p in h.seats} - {"Hero"}
        table = statlib.tally_group(hands, names, "HU")
        every = analyze(hands)
        merged = field.merge_stats([every[n] for n in names])
        for key in ("vpip", "bb_vs_open.raise", "vs_cbet_flop.fold"):
            stat = next(s for s in statlib.available("HU") if s.src["HU"] == key and not s.positions)
            found = statlib.value(stat, "HU", table).ratio
            self.assertEqual((found.hits, found.opps), (merged.r(key).hits, merged.r(key).opps), key)

    def test_band_refs(self):
        fold = statlib.BY_ID["fold_cbet_f"]
        self.assertEqual(statlib.field_ref(fold, "ring"), field.RING_DEF["fold_cbet"].ref)
        self.assertEqual(statlib.field_ref(fold, "ring", "micro"), field.BAND_REFS["micro"]["fold_cbet"])
        self.assertEqual(statlib.field_ref(fold, "HU", "hautes"), field.HU_BAND_REFS["hautes"]["vs_cbet_flop.fold"])
        self.assertEqual(statlib.field_ref(statlib.BY_ID["fold_3bet"], "HU", "hautes"),
                         STAT_DEFS["sb_vs_3bet.fold"].ref)  # le préflop : le solveur, à tous les niveaux
        self.assertEqual(statlib.field_ref(statlib.BY_ID["rfi_co"], "ring", "micro"), ring.OPEN_6MAX["CO"])
        self.assertIsNone(statlib.field_ref(statlib.BY_ID["squeeze"], "ring", "micro"))
        self.assertEqual(settings.check({"field_stat_selection": {"HU": ["vpip"]}}),
                         {"field_stat_selection": {"HU": ["vpip"]}})
        with self.assertRaises(ValueError):
            settings.check({"field_stat_selection": {"HU": ["squeeze"]}})
        self.assertEqual([s.id for s in statlib.selection("ring", None, statlib.FIELD_DEFAULTS)],
                         statlib.FIELD_DEFAULTS["ring"])


class ReadTest(unittest.TestCase):
    """Les nouvelles stats, coup par coup."""

    def test_preflop(self):
        squeeze = table_hand(1, "SB", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", CALL), ("SB", RAISE, 22.0), ("BB", FOLD), ("CO", FOLD), ("BTN", FOLD)])
        self.assertEqual(ratio([squeeze], "squeeze"), (1, 1))
        self.assertEqual(ratio([squeeze], "threebet"), (1, 1))
        three = table_hand(2, "CO", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", RAISE, 15.0), ("SB", FOLD), ("BB", FOLD), ("CO", CALL)])
        for sid, want in (("call_3bet", (1, 1)), ("fold_3bet", (0, 1)), ("fourbet", (0, 1)), ("fourbet_any", (0, 1)),
                          ("rfi", (1, 1)), ("rfi_co", (1, 1)), ("steal", (1, 1)), ("rfi_btn", (None, 0))):
            self.assertEqual(ratio([three], sid), want, sid)
        self.assertAlmostEqual(statlib.value(statlib.BY_ID["open_size"], "ring",
                                             statlib.tally([three], "Hero", "ring")).value, 2.5)
        four = table_hand(3, "BTN", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", RAISE, 15.0), ("SB", RAISE, 40.0), ("BB", FOLD), ("CO", FOLD), ("BTN", FOLD)])
        self.assertEqual(ratio([four], "threebet_size"), (None, 1))
        self.assertAlmostEqual(statlib.value(statlib.BY_ID["threebet_size"], "ring",
                                             statlib.tally([four], "Hero", "ring")).value, 3.0)
        cold = table_hand(4, "SB", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", RAISE, 15.0), ("SB", RAISE, 40.0), ("BB", FOLD), ("CO", FOLD), ("BTN", FOLD)])
        self.assertEqual(ratio([cold], "fourbet_any"), (1, 1))  # cold 4bet
        self.assertEqual(ratio([cold], "fourbet"), (None, 0))  # il n'avait pas ouvert
        folds = table_hand(5, "BTN", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", RAISE, 15.0), ("SB", FOLD), ("BB", FOLD), ("CO", RAISE, 40.0), ("BTN", FOLD)])
        self.assertEqual(ratio([folds], "fold_4bet"), (1, 1))
        limps = [("UTG", CALL), ("HJ", CALL), ("CO", RAISE, 10.0), ("BTN", FOLD), ("SB", FOLD), ("BB", FOLD),
                 ("UTG", FOLD), ("HJ", CALL)]
        self.assertEqual(ratio([table_hand(6, "HJ", ["Ah", "Kd"], limps)], "overlimp"), (1, 1))
        self.assertEqual(ratio([table_hand(6, "HJ", ["Ah", "Kd"], limps)], "limp_call"), (1, 1))
        self.assertEqual(ratio([table_hand(6, "UTG", ["Ah", "Kd"], limps)], "limp_fold"), (1, 1))
        self.assertEqual(ratio([table_hand(6, "CO", ["Ah", "Kd"], limps)], "iso"), (1, 1))
        steal = table_hand(7, "BB", ["Ah", "Kd"], FOLDS_TO["BTN"] + [("BTN", RAISE, 5.0), ("SB", FOLD), ("BB", CALL)])
        for sid, want in (("call_steal", (1, 1)), ("fold_steal", (0, 1)), ("fold_steal_bb", (0, 1)),
                          ("fold_steal_sb", (None, 0)), ("threebet_steal", (0, 1)), ("fold_open", (0, 1))):
            self.assertEqual(ratio([steal], sid), want, sid)

    def test_postflop_initiative(self):
        hand = co_vs_bb(1, "CO", ["Ah", "Kd"], [("flop", "BB", CHECK), ("flop", "CO", BET, 6.0),
                                                ("flop", "BB", RAISE, 20.0), ("flop", "CO", FOLD)], winner="BB")
        for sid, want in (("cbet_f", (1, 1)), ("cbet_f_ip", (1, 1)), ("cbet_f_srp", (1, 1)), ("cbet_f_hu", (1, 1)),
                          ("cbet_f_oop", (None, 0)), ("fold_raise_f", (1, 1)), ("bet_f", (1, 1))):
            self.assertEqual(ratio([hand], sid), want, sid)
        table = statlib.tally([hand], "Hero", "ring")
        self.assertAlmostEqual(statlib.value(statlib.BY_ID["size_cbet_f"], "ring", table).value, 600 / 11)
        defend = co_vs_bb(2, "BB", ["Ah", "Kd"], [("flop", "BB", CHECK), ("flop", "CO", BET, 6.0),
                                                  ("flop", "BB", RAISE, 20.0), ("flop", "CO", FOLD)])
        for sid, want in (("xr_f", (1, 1)), ("raise_cbet_f", (1, 1)), ("fold_cbet_f", (0, 1)),
                          ("raise_bet_f", (1, 1)), ("xf_f", (0, 1)), ("donk_f", (0, 1)), ("afq", (1, 1))):
            self.assertEqual(ratio([defend], sid), want, sid)
        table = statlib.tally([defend], "Hero", "ring")
        self.assertAlmostEqual(statlib.value(statlib.BY_ID["size_raise_f"], "ring", table).value, 20 / 6)
        self.assertIsNone(statlib.value(statlib.BY_ID["af"], "ring", table).value)  # aucun call
        multi = table_hand(3, "CO", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", CALL), ("SB", FOLD), ("BB", CALL)],
            [("flop", "BB", CHECK), ("flop", "CO", BET, 8.0), ("flop", "BTN", FOLD), ("flop", "BB", CALL)])
        self.assertEqual(ratio([multi], "cbet_f_multi"), (1, 1))
        self.assertEqual(ratio([multi], "cbet_f_oop"), (1, 1))  # le bouton parle après lui

    def test_postflop_defence(self):
        probe = [("flop", "BB", CHECK), ("flop", "CO", CHECK), ("turn", "BB", BET, 5.0), ("turn", "CO", CALL),
                 ("river", "BB", CHECK), ("river", "CO", BET, 10.0), ("river", "BB", FOLD)]
        self.assertEqual(ratio([co_vs_bb(1, "BB", ["Ah", "Kd"], probe, winner="CO")], "probe_t"), (1, 1))
        self.assertEqual(ratio([co_vs_bb(1, "BB", ["Ah", "Kd"], probe, winner="CO")], "xf_r"), (1, 1))
        self.assertEqual(ratio([co_vs_bb(1, "CO", ["Ah", "Kd"], probe, winner="CO")], "call_bet_t"), (1, 1))
        self.assertEqual(ratio([co_vs_bb(1, "CO", ["Ah", "Kd"], probe, winner="CO")], "delayed_cbet_t"), (None, 0))
        delayed = [("flop", "BB", CHECK), ("flop", "CO", CHECK), ("turn", "BB", CHECK), ("turn", "CO", BET, 5.0),
                   ("turn", "BB", FOLD)]
        self.assertEqual(ratio([co_vs_bb(2, "CO", ["Ah", "Kd"], delayed)], "delayed_cbet_t"), (1, 1))
        self.assertEqual(ratio([co_vs_bb(2, "BB", ["Ah", "Kd"], delayed, winner="CO")], "probe_t"), (0, 1))
        donk = [("flop", "BB", CHECK), ("flop", "CO", BET, 5.0), ("flop", "BB", CALL), ("turn", "BB", BET, 10.0),
                ("turn", "CO", FOLD)]
        self.assertEqual(ratio([co_vs_bb(3, "BB", ["Ah", "Kd"], donk)], "donk_t"), (1, 1))
        self.assertEqual(ratio([co_vs_bb(3, "BB", ["Ah", "Kd"], donk)], "call_cbet_f"), (1, 1))
        self.assertEqual(ratio([co_vs_bb(3, "CO", ["Ah", "Kd"], donk, winner="BB")], "cbet_t"), (None, 0))
        sb = FOLDS_TO["SB"] + [("SB", RAISE, 6.0), ("BB", CALL)]
        stab = table_hand(4, "BB", ["Ah", "Kd"], sb, [("flop", "SB", CHECK), ("flop", "BB", BET, 4.0),
                                                      ("flop", "SB", FOLD)])
        self.assertEqual(ratio([stab], "stab_f"), (1, 1))
        self.assertEqual(ratio([table_hand(4, "SB", ["Ah", "Kd"], sb, [("flop", "SB", CHECK), ("flop", "BB", BET, 4.0),
                                                                       ("flop", "SB", FOLD)], winner="BB")],
                               "cbet_f_oop"), (0, 1))
        float_turn = table_hand(5, "BB", ["Ah", "Kd"], sb, [
            ("flop", "SB", BET, 4.0), ("flop", "BB", CALL), ("turn", "SB", CHECK), ("turn", "BB", BET, 10.0),
            ("turn", "SB", FOLD)])
        self.assertEqual(ratio([float_turn], "stab_t"), (1, 1))
        self.assertEqual(ratio([float_turn], "fold_cbet_f"), (0, 1))


class PageTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        folder = self.home.parent / "mains"
        folder.mkdir()
        shutil.copy(FIXTURES / "betclic_sample.txt", folder / "hu.txt")
        shutil.copy(SITES / "betclic_6max.txt", folder / "six.txt")
        from analyzer.app.library import Library
        self.lib = Library(folder)

    def table(self, page: str) -> str:
        return page[page.index("legend-dev"):page.index("Position par position")]

    def test_choose(self):
        page = self.lib.self_page("stats", table_format="ring")
        for text in ("Choisir mes stats", 'id="sl-data"', "Préflop — face à une ouverture", "data-label=\"Squeeze\"",
                     "Position par position", ">Fold face au vol</abbr>"):
            self.assertIn(text, page)
        self.assertNotIn(">Squeeze</abbr>", self.table(page))  # au catalogue, pas dans tes stats
        self.lib.set_settings({"stat_selection": {"ring": ["squeeze", "vpip", "size_cbet_f"]}})
        table = self.table(self.lib.self_page("stats", table_format="ring"))
        self.assertLess(table.index(">Squeeze</abbr>"), table.index(">VPIP</abbr>"))  # dans ton ordre
        self.assertIn(">Taille de la c-bet flop</abbr>", table)
        self.assertNotIn(">Fold face au vol</abbr>", table)
        heads_up = self.lib.self_page("stats")
        self.assertIn(">Open (RFI)</abbr>", self.table(heads_up))  # l'autre format garde la sélection par défaut
        self.assertNotIn("data-label=\"Squeeze\"", heads_up)  # pas au catalogue du heads-up
        self.lib.set_settings({"stat_selection": None})
        self.assertIn(">Fold face au vol</abbr>", self.table(self.lib.self_page("stats", table_format="ring")))
        self.lib.set_settings({"stat_selection": {"ring": []}})
        self.assertIn("Aucune stat choisie", self.lib.self_page("stats", table_format="ring"))


if __name__ == "__main__":
    unittest.main()

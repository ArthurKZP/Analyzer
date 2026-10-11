"""Le plan de jeu contre le field : les leaks d'une population de l'Étude du field, traduits en ajustements du plan
suggéré (field_plan), et les populations proposées (Library.plan_field)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from analyzer import field, field_plan, players
from analyzer.insights import STAT_DEFS, Finding
from analyzer.stats import Ratio
from tests.base import IsolatedHome
from tests.test_field import BB, CO, barrel_hand

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"
BASE = {"cbet": 0.55, "fold_cbet": 0.30, "raise_cbet": 0.12, "fold_xr": 0.40, "barrel": 0.50, "river_bluff": 0.40,
        "river_value": 0.45, "fold_turn": 0.35, "fold_river": 0.45, "stab": None, "probe": 0.40}


def study(leaks, table_format="ring"):
    return field.Study(["A", "B"], table_format, 500, {}, leaks, ("autre", []), [], [], [], {}, {}, "micro")


class AdjustmentsTest(unittest.TestCase):
    def test_ring_population(self):
        ratios = {"fold_cbet": Ratio(70, 100), "fold_river": Ratio(20, 100), "cbet_hu": Ratio(85, 100),
                  "wtsd": Ratio(40, 100), "vpip": Ratio(45, 100), "afq": Ratio(10, 20)}
        found = field_plan.adjustments(BASE, study(field.ring_findings(ratios, "micro")))
        attack = {a["concept"]: a for a in found["attack"]}
        self.assertEqual(set(attack), {"fold_cbet", "fold_river", "wtsd"})  # le VPIP : avant le flop, pas ici
        self.assertIn("Le plan c-bet 55 % de la range ; contre eux, vers 65 %", attack["fold_cbet"]["text"])
        self.assertIn("le plan bluffe 40 % de ses mains « rien » ; contre eux, vers 25 %", attack["fold_river"]["text"])
        self.assertEqual(attack["fold_cbet"]["fact"], "Folde trop face à la c-bet")
        self.assertIn("repère 38–55 %", attack["fold_cbet"]["numbers"])  # les repères des micro limites
        self.assertTrue(attack["fold_cbet"]["strong"])
        defense = {a["concept"]: a for a in found["defense"]}
        self.assertIn("Face à la c-bet, le plan folde 30 % ; contre eux, vers 20 %", defense["cbet"]["text"])
        self.assertFalse(defense["afq"]["strong"])  # 50 % sur 20 occasions (repère 30–46 % aux micro limites)
        self.assertEqual(found["defense"][-1]["concept"], "afq")  # les leaks à confirmer après les solides
        no_plan = field_plan.adjustments(dict(BASE, cbet=None), study(field.ring_findings(ratios, "micro")))
        self.assertNotIn("fold_cbet", {a["concept"] for a in no_plan["attack"]})  # sans chiffre du plan : rien

    def test_heads_up_population(self):
        leaks = [Finding(STAT_DEFS[k], Ratio(60, 100), d, True, getattr(STAT_DEFS[k], "high" if d == "haut" else "low"))
                 for k, d in (("cbet_flop_ip", "haut"), ("cbet_flop_oop", "bas"), ("vs_cbet_river.fold", "haut"),
                              ("vs_bet_river.fold", "haut"))]
        found = field_plan.adjustments(BASE, study(leaks, "HU"), aggressor_ip=True)
        self.assertEqual([a["concept"] for a in found["defense"]], ["cbet"])  # la c-bet en position seulement
        self.assertEqual(found["defense"][0]["title"], "Défends un peu plus face à la c-bet")
        self.assertEqual([a["concept"] for a in found["attack"]], ["fold_river"])  # deux stats, un ajustement
        found = field_plan.adjustments(BASE, study(leaks, "HU"), aggressor_ip=False)
        self.assertEqual(found["defense"][0]["title"], "Respecte leurs c-bets, attaque leurs checks")


class LibraryTest(IsolatedHome):
    def test_plan_field(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        shutil.copy(FIXTURE, Path(tmp.name) / "sample.txt")
        from analyzer.app.library import Library
        lib = Library(Path(tmp.name))
        self.addCleanup(lib.solves.shutdown)
        ring = [barrel_hand(k, ["8c", "6c"] if k % 3 else ["Kh", "Qh"]) for k in range(60)]
        with lib._lock:
            lib._all_ring = ring
            lib._select()
        players.set_kind(BB, "rec")
        found = lib.plan_field()
        self.assertEqual(set(found), {"HU", "ring"})
        ring_field = found["ring"]
        self.assertEqual((ring_field["band"], ring_field["bands"]), ("hautes", [("hautes", 60)]))
        pops = {p["key"]: p for p in ring_field["populations"]}
        self.assertEqual(pops["reg"]["label"], "Les réguliers")
        self.assertIn(CO, pops["reg"]["study"].names)
        self.assertEqual(pops["rec"]["players"], 1)
        self.assertTrue(any(k.startswith("rec-") for k in pops) or pops["rec"]["study"].style[0] == "autre")
        self.assertEqual(found["HU"]["populations"][0]["key"], "reg")  # Villain, en heads-up
        page = lib.plan_page("micro")  # un niveau sans mains : celui où tu en as le plus
        self.assertIn("Plan de jeu suggéré", page)


if __name__ == "__main__":
    unittest.main()

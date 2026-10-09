import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from analyzer import bluffs
from analyzer.app.bluffs_page import build_bluffs_page
from analyzer.parsers import load_hands
from analyzer.stats import Ratio

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

FIXTURES = Path(__file__).parent / "fixtures"


def shown(street, intent, line="Barrel", size="grosse (56–120 %)", value="over", theory=0.25, think=None, k=[0]):
    k[0] += 1
    return bluffs.Shown("V", SimpleNamespace(hand_id=f"H{k[0]}", date=None), 3, street, line, size, value, intent,
                        "desc", theory, think)


class FeatureTest(unittest.TestCase):
    def test_feature(self):
        board = ["Ks", "7d", "2c", "Ah", "7s"]
        self.assertEqual(bluffs.feature(board, "flop"), "King high")
        self.assertEqual(bluffs.feature(board, "turn"), "over")
        self.assertEqual(bluffs.feature(board, "river"), "paired")
        self.assertIsNone(bluffs.feature(board[:3], "turn"))
        self.assertEqual(bluffs.feature_label("turn", "flush"), "Couleur possible")

    def test_compare(self):
        self.assertEqual(bluffs.compare(Ratio(20, 20), 0.5, 5), ("plus", "solide"))
        self.assertEqual(bluffs.compare(Ratio(5, 5), 0.3, 5), ("plus", "à confirmer"))  # net, mais 5 occasions
        self.assertEqual(bluffs.compare(Ratio(0, 8), 0.48, 5), ("moins", "à confirmer"))
        self.assertIsNone(bluffs.compare(Ratio(3, 10), 0.35, 5))
        self.assertIsNone(bluffs.compare(Ratio(4, 4), 0.1, 5))  # trop peu d'occasions


class FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hands = list(load_hands([FIXTURES]))

    def test_frequencies_and_shown(self):
        with mock.patch.object(bluffs, "solver_rates", return_value={("cbet_flop", "King high"): 0.6}):
            report = bluffs.analyze(self.hands, ["Hero"], "Villain")
        rows = {(f.family, f.spot.key, f.feature): f for f in report.freqs}
        cbet = rows[("srp", "cbet_flop", "King high")]  # HAND01 : c-bet sur K♣7♥2♠ en pot simple
        self.assertEqual((cbet.ratio.hits, cbet.ratio.opps, cbet.solver), (1, 1, 0.6))
        self.assertEqual(rows[("3bet", "cbet_turn", "brick")].ratio.opps, 1)  # turn 3♦ sur Q♣8♠2♠ : pas de barrel
        self.assertIn(("3bet", "other_river", "straight"), rows)
        # la c-bet de HAND02 (Q♥J♥ sur Q♣8♠2♠) vue à l'abattage : de la value
        self.assertEqual([(s.street, s.line, s.feature, s.intent) for s in report.shown],
                         [("flop", "C-bet", "Queen high", "value")])

        villain = bluffs.analyze(self.hands, ["Villain"], "Hero")
        river = villain.shown[0]
        self.assertEqual((river.street, river.intent, river.bluff), ("river", "bluff", True))
        self.assertAlmostEqual(river.theory, 0.125, places=3)  # équité qu'il te fallait pour payer
        page = build_bluffs_page(villain, "de Villain")
        for text in ("Ce qui ressort", "Ses mises selon la carte", "Ses mains montrées", "Ses bluffs vus à l'abattage"):
            self.assertIn(text, page)


class PatternTest(unittest.TestCase):
    def test_river_groups_against_theory(self):
        items = [shown("river", "bluff", line="Mise après check au turn") for _ in range(8)]
        items += [shown("river", "value", line="Mise après check au turn") for _ in range(2)]
        items += [shown("river", "value", line="Relance", size="", value="brick", theory=0.3) for _ in range(5)]
        groups = bluffs.shown_groups(items)
        line = next(g for g in groups if g.dimension == "line" and g.value == "Mise après check au turn")
        self.assertEqual((line.ratio.hits, line.ratio.opps, line.reference), (8, 10, 0.25))
        self.assertEqual(line.verdict, ("plus", "solide"))
        self.assertFalse(any(g.dimension == "size" and not g.value for g in groups))  # pas de tranche pour une relance
        raise_group = next(g for g in groups if g.value == "Relance")
        self.assertEqual(raise_group.verdict, ("moins", "à confirmer"))
        found = bluffs.patterns([], groups, [])
        titles = [p.title for p in found]
        self.assertIn("À la river, sa ligne « Mise après check au turn » : souvent des bluffs", titles)
        # les 10 mêmes mains forment aussi le groupe « overcard » et le groupe de taille : comptées une fois
        self.assertEqual(sum("Mise après check" in t or "overcard" in t or "grosse" in t for t in titles), 1)
        self.assertEqual(found[0].confidence, "solide")

    def test_frequency_pattern_and_timing(self):
        f = bluffs.Freq("srp", bluffs.SPOT["cbet_turn"], "over", Ratio(18, 20), solver=0.5)
        f.verdict = bluffs.compare(f.ratio, f.solver, bluffs.MIN_FREQ)
        items = [shown("turn", "bluff", think=9.0) for _ in range(3)] + [shown("turn", "value", think=2.0) for _ in range(3)]
        times = bluffs.timing(items)
        self.assertEqual(times, [{"street": "turn", "bluff": 9.0, "value": 2.0, "n": (3, 3)}])
        found = bluffs.patterns([f], bluffs.shown_groups(items), times)
        first = found[0]
        self.assertEqual(first.title, "2e barrel (SRP) : il mise bien plus que la théorie quand une overcard tombe")
        # de qui on parle : un joueur nommé, ou les réguliers (au pluriel)
        named = bluffs.patterns([f], bluffs.shown_groups(items), times, bluffs.Voice("Villain"))
        self.assertEqual(named[0].title, "2e barrel (SRP) : Villain mise bien plus que la théorie quand une overcard tombe")
        many = bluffs.patterns([f], bluffs.shown_groups(items), times, bluffs.Voice("les réguliers", plural=True))
        self.assertEqual(many[0].title,
                         "2e barrel (SRP) : les réguliers misent bien plus que la théorie quand une overcard tombe")
        self.assertIn("relance-les", many[0].advice)
        self.assertTrue(any(p.source == "timing" and "les réguliers réfléchissent plus longtemps quand ils bluffent"
                            in p.title for p in many))
        self.assertIn("90 % sur 20 occasions, contre 50 % pour le solveur", first.evidence)
        self.assertIn("3 sans main faite sur 6", first.evidence)  # ses mises montrées sur ces cartes
        self.assertTrue(any(p.source == "timing" and "plus longtemps" in p.title for p in found))


if __name__ == "__main__":
    unittest.main()


def freq(spot, players, solver=0.5, feature="over", family="srp"):
    f = bluffs.Freq(family, bluffs.SPOT[spot], feature, solver=solver)
    for name, (hits, opps) in players.items():
        f.players[name] = Ratio(hits, opps)
        f.ratio = Ratio(f.ratio.hits + hits, f.ratio.opps + opps)
    f.sample = bluffs.pooled(f.players, bluffs.PLAYER_K)
    return f


class PopulationTest(unittest.TestCase):
    def test_pooled(self):
        one = bluffs.pooled({"A": Ratio(9, 10)}, bluffs.PLAYER_K)
        self.assertEqual((one.hits, one.opps), (9, 10))  # un seul joueur : tel quel
        # un régulier à 1000 occasions qui mise 90 %, trois à 40 occasions qui misent 30 % : en tout 82 %,
        # mais la moyenne des joueurs ne laisse pas le gros volume décider seul
        many = bluffs.pooled({"Gros": Ratio(900, 1000), "B": Ratio(12, 40), "C": Ratio(12, 40), "D": Ratio(12, 40)},
                             bluffs.PLAYER_K)
        rate = many.hits / many.opps
        self.assertLess(rate, 0.5)
        self.assertAlmostEqual(rate, (0.98 * 0.9 + 3 * (2 / 3) * 0.3) / (0.98 + 2), places=2)
        self.assertLess(many.opps, 1120)  # l'intervalle suit l'effectif efficace, pas les 1120 occasions
        self.assertEqual(bluffs.pooled({}, 5).opps, 0)

    def test_profiles_and_clusters(self):
        # deux façons de jouer : les « barreleurs » (2e et 3e barrels bien plus que le solveur), les autres comme lui
        heavy = {"A": (160, 200), "B": (80, 100), "C": (45, 60)}
        normal = {"D": (100, 200), "E": (50, 100)}
        freqs = [freq("cbet_turn", {**heavy, **normal}), freq("cbet_river", {**heavy, **normal}),
                 freq("cbet_flop", {n: (o // 2, o) for n, (_, o) in {**heavy, **normal}.items()})]
        freqs.append(freq("cbet_flop", {"F": (5, 10)}))  # trop peu de mains pour un profil
        hands = {"A": 900, "B": 400, "C": 200, "D": 800, "E": 300, "F": 30}
        profs = {p.name: p for p in bluffs.profiles(freqs, [], hands)}
        pop = bluffs.pooled({n: Ratio(h, o) for n, (h, o) in {**heavy, **normal}.items()}, bluffs.PLAYER_K)
        self.assertAlmostEqual(profs["A"].gaps["cbet_turn"], (60 + 15 * (pop.hits / pop.opps - 0.5)) / 215)
        self.assertGreater(profs["A"].gaps["cbet_turn"], 0.2)
        self.assertLess(abs(profs["D"].gaps["cbet_turn"]), 0.1)
        self.assertGreater(profs["A"].share, profs["A"].weight)  # gros volume : moins de poids que d'occasions
        groups = bluffs.clusters(list(profs.values()))
        self.assertEqual([g.names for g in groups], [["A", "B", "C"], ["D", "E"]])  # F : pas de profil
        self.assertEqual([p.group for p in (profs["A"], profs["D"], profs["F"])], [1, 2, None])
        self.assertIn(groups[0].traits[0][0], ("cbet_turn", "cbet_river"))
        self.assertTrue(groups[0].title.startswith(("2e barrel plus souvent", "3e barrel plus souvent")))
        self.assertTrue(groups[1].title.endswith("moins souvent"))
        report = bluffs.Report(list(hands), 2630, 0, freqs, [], [], [], [], list(profs.values()), groups)
        page = build_bluffs_page(report, "des réguliers", groups=[{"cluster": g, "patterns": []} for g in groups],
                                 players=[{"name": n, "hands": h, "shown": 0, "river": 0, "top": None,
                                           "weight": profs[n].weight, "share": profs[n].share, "group": profs[n].group}
                                          for n, h in hands.items()])
        for text in ("Profils des réguliers", "A (900), B (400), C (200)", "Trop peu de mains pour un profil : 1 régulier",
                     "Part des occasions", "moyenne des joueurs", "Leurs mises selon la carte", "Ils misent"):
            self.assertIn(text, page)
        self.assertNotIn("Il mise", page)  # plusieurs joueurs : on parle d'eux au pluriel

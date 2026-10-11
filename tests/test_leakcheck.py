"""Les écarts à la théorie vérifiés en jeu (analyzer/leakcheck.py) : une fuite qui coûte des bb, ou une exploitation
de ses adversaires (c-bet range quand ils se couchent trop, open large quand ils ne défendent pas…) ; les grosses
erreurs d'abord dans les leaks, les écarts justifiés à part."""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from analyzer import leakcheck, leaks, ring_leaks
from analyzer.app.leaks_page import build_leaks_page
from analyzer.leakcheck import Evidence, Point, Reply, Value
from analyzer.models import BET, CALL, CHECK, FOLD, POST_BB, POST_SB, RAISE, Action, Hand, Seat
from analyzer.stats import Ratio
from analyzer.theory import ring_ranges
from tests.base import IsolatedHome, isolate_module, release_module
from tests.test_ring_leaks import LINES, POSITIONS, table_hand


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()


BOARD = ("2c", "7d", "9s", "Jh", "3c")
HANDS = (("Ah", "Kd"), ("9h", "8h"), ("As", "5s"), ("7c", "7d"))  # quatre familles : aucune ne perd assez pour un leak
OPEN_CALL = [("preflop", "BTN", RAISE, 5.0), ("preflop", "BB", CALL)]
# Les repères du solveur (sans flop résolu dans le dossier de test) : il c-bet 60 %, la BB se couche 30 % face à la
# c-bet et relance 15 % des mains qui continuent.
BASE = {"agresseur": {"cbet": (0.6, 24)}, "defenseur": {"fold_flop": (0.3, 24), "raise": (0.15, 24)}}


def hu_hand(n, steps, winner, cards=("Ah", "Kd"), board=BOARD, villain="Villain"):
    """Une main heads-up à 1/2, Hero au bouton : steps, [(street, « BTN » | « BB », action, montant)] après les
    blindes (relance : le total ; mise : son montant). Le gagnant (un nom) prend tout le pot."""
    names = {"BTN": "Hero", "BB": villain}
    seats = {"Hero": Seat("Hero", 1, 200.0, is_button=True, is_hero=True), villain: Seat(villain, 2, 200.0)}
    actions, state = [], {"put": dict.fromkeys(seats, 0.0), "pot": 0.0, "street": "preflop"}

    def add(street, seat, kind, value=0.0):
        if street != state["street"]:
            state["street"], state["put"] = street, dict.fromkeys(seats, 0.0)
        put, player = state["put"], names[seat]
        if kind == RAISE:
            to, amount = value, value - put[player]
        elif kind == CALL:
            to = max(put.values())
            amount = to - put[player]
        elif kind in (BET, POST_SB, POST_BB):
            to, amount = put[player] + value, value
        else:
            to, amount = put[player], 0.0
        actions.append(Action(player, kind, street, amount, to, pot_before=state["pot"]))
        put[player] = to
        state["pot"] += amount

    add("preflop", "BTN", POST_SB, 1.0)
    add("preflop", "BB", POST_BB, 2.0)
    for step in steps:
        add(*step)
    pot = round(state["pot"], 2)
    hand = Hand("Test", f"H{n:04d}", "T1", "NLHE 1/2 HU", datetime(2026, 10, 1, 12) + timedelta(minutes=n), 1.0, 2.0,
                pot, 0.0, 2, seats, {"Hero": list(cards)}, list(board), actions, {winner: pot})
    hand.finalize()
    return hand


def cbet_hand(n, folds):
    """Open du bouton payé, c-bet de 3 dans un pot de 10 : la BB se couche, ou paie et gagne à l'abattage."""
    steps = OPEN_CALL + [("flop", "BB", CHECK), ("flop", "BTN", BET, 3.0)]
    if folds:
        return hu_hand(n, steps + [("flop", "BB", FOLD)], "Hero", HANDS[n % 4])
    steps += [("flop", "BB", CALL), ("turn", "BB", CHECK), ("turn", "BTN", CHECK), ("river", "BB", CHECK),
              ("river", "BTN", CHECK)]
    return hu_hand(n, steps, "Villain", HANDS[n % 4])


def point(kind, pot=5.0, size=1.5, reply=None, value=0.0, intent=None):
    return Point(Action("Hero", kind, "flop", 2 * size, 2 * size), value, intent, pot, size, reply)


def stat(hits, opps, ref=0.6):
    return leaks.Stat("srp:cbet", "SRP", "BTN : C-bet flop", "bet",
                      {"all": Ratio(hits, opps), "reg": Ratio(hits, opps), "rec": Ratio()}, ref, per100=20, stake=2.0)


class ValueTest(unittest.TestCase):
    def test_measure(self):
        self.assertIsNone(leakcheck.measure([1.0] * 10))  # trop peu de décisions
        even = leakcheck.measure([1.0, -1.0] * 10)
        self.assertEqual((even.n, even.mean, even.sign), (20, 0.0, 0))
        clear = leakcheck.measure([2.0, 1.0] * 10)
        self.assertEqual(((-clear).sign, clear.sign), (-1, 1))
        diff = clear.minus(even)
        self.assertAlmostEqual(diff.mean, 1.5)
        self.assertGreater(diff.hi - diff.lo, clear.hi - clear.lo)  # deux incertitudes

    def test_folds_value(self):
        bets = [point(BET, reply=FOLD)] * 30 + [point(BET, reply=CALL)] * 10
        value, text = leakcheck.folds_value(bets, "tes c-bets", reference=0.3)
        # ils se couchent 75 % au lieu de 30 % : chaque point de fold en plus rapporte le pot et la mise (6,5 bb)
        self.assertAlmostEqual(value.mean, 0.45 * 6.5)
        self.assertEqual(value.sign, 1)
        self.assertIn("ils se couchent 75 % (40 fois, à 30 % du pot en moyenne), contre 30 % face à la théorie : "
                      "chaque mise d'une main faible en plus de la théorie rapporte 2,93 bb", text)
        # le fold du solveur à sa taille (un bluff pur y est rentable dès 25 %), ramené à la tienne (23 %)
        scaled, text = leakcheck.folds_value(bets, "tes c-bets", reference=0.3, ref_alpha=0.25)
        self.assertAlmostEqual(scaled.mean, (0.75 - 0.3 * (1.5 / 6.5) / 0.25) * 6.5)
        self.assertIn("contre 28 % attendus face à la théorie à ta taille", text)
        _, text = leakcheck.folds_value(bets, "tes opens", "open", reference=0.3, sized=False)
        self.assertTrue(text.startswith("Face à tes opens, ils se couchent 75 % (40 fois), contre 30 % face à la "
                                        "théorie : chaque open"))
        pure, text = leakcheck.folds_value(bets, "tes c-bets")  # sans repère : le bluff pur
        self.assertAlmostEqual(pure.mean, 0.75 * 5 - 0.25 * 1.5)
        self.assertIn("un bluff pur y est rentable dès 23 % de folds : il rapporte 3,3", text)
        self.assertEqual(leakcheck.folds_value(bets[:10], "tes c-bets"), (None, None))

    def test_fold_evidence(self):
        """Folder plus souvent face à la c-bet : l'inverse de ce que rapportent ses calls avec une main moyenne."""
        fold_flop = next(s for s in leakcheck.exploit.situations("srp", "defenseur") if s.key == "fold_flop")
        found = [point(CALL, value=2.0, intent="thin"), point(CALL, value=1.0, intent="semi")] * 10 + \
            [point(CALL, value=-30.0, intent="value")] * 5  # ses mains fortes ne comptent pas
        evidence = leakcheck.postflop_evidence(fold_flop, found)
        self.assertAlmostEqual(evidence.value.mean, -1.5)
        self.assertEqual(evidence.value.sign, -1)
        self.assertIn("+1,50 bb chacun à partir de là (20 fois", evidence.lines[0])


class JudgeTest(unittest.TestCase):
    def test_measured(self):
        more = stat(90, 100)  # c-bet 90 % pour 60 % : 100 occasions sur 500 mains, 6 c-bets en plus pour 100 mains
        check = leakcheck.judge(more, Evidence(Value(40, 2.0, 1.0, 3.0), ["preuve"]), [], hands=500)
        self.assertEqual((check.verdict, check.measured, check.proofs), ("exploit", True, ["preuve"]))
        self.assertAlmostEqual(check.bb100, 12.0)
        self.assertEqual(check.badge, "rapporte +12,0 bb/100")
        self.assertIn("te rapporte environ +12,0 bb/100", check.summary)
        loses = leakcheck.judge(more, Evidence(Value(40, -1.0, -1.5, -0.5)), [], 500)
        self.assertEqual((loses.verdict, round(loses.cost, 6), loses.badge), ("fuite", 6.0, "coûte 6,0 bb/100"))
        # moins de c-bets que la théorie, alors que miser rapporte : chaque c-bet qui manque coûte
        fewer = leakcheck.judge(stat(30, 100), Evidence(Value(40, 1.0, 0.5, 1.5)), [], 500)
        self.assertEqual((fewer.verdict, round(fewer.cost, 6)), ("fuite", 6.0))
        self.assertIsNone(leakcheck.judge(stat(60, 100), Evidence(Value(40, 1.0, 0.5, 1.5)), [], 500))  # pas d'écart

    def test_replies_and_uncertain(self):
        more = stat(90, 100)
        unsure = Evidence(Value(40, 0.2, -0.5, 0.9))
        folds = Reply("face à tes c-bets, ils se couchent", Ratio(60, 100), 0.3, sign=1)
        check = leakcheck.judge(more, unsure, [folds], 500)
        self.assertEqual((check.verdict, check.measured, check.badge), ("exploit", False, "justifié"))
        self.assertIn("Face à tes c-bets, ils se couchent 60 % (100 fois), plus que le solveur (30 %).", check.proofs)
        raises = Reply("quand ils continuent face à ta c-bet, ils relancent", Ratio(40, 100), 0.15, sign=-1)
        self.assertEqual(leakcheck.judge(more, unsure, [folds, raises], 500).verdict, "")  # ils se contredisent
        alone = leakcheck.judge(more, unsure, [], 500)
        # rien de net : la mesure incertaine borne ce que l'écart peut coûter (0,5 bb × 6 décisions pour 100 mains)
        self.assertEqual((alone.verdict, round(alone.max_cost, 2)), ("", 3.0))
        self.assertEqual(alone.badge, "coût ≤ 3,0 bb/100")
        few = Reply("face à tes c-bets, ils se couchent", Ratio(12, 15), 0.3, sign=1)
        self.assertEqual(few.lean, 0)  # trop peu d'occasions pour juger

    def test_plan(self):
        near = stat(80, 100)  # le plan de jeu c-bet 78 % (range sur les flops où le solveur mise presque tout)
        check = leakcheck.judge(near, Evidence(), [], 500, plan=(0.78, 24))
        self.assertEqual(check.verdict, "plan")
        self.assertIn("Le plan de jeu suggéré c-bet 78 %", check.proofs[-1])
        self.assertEqual(leakcheck.judge(near, Evidence(), [], 500, plan=(0.61, 24)).verdict, "")  # le plan = le solveur
        self.assertEqual(leakcheck.judge(stat(95, 100), Evidence(), [], 500, plan=(0.75, 24)).verdict, "")  # trop loin

    def test_chance(self):
        """Il c-bet comme la théorie, mais ils se couchent trop : c-bet plus souvent rapporterait."""
        like = stat(61, 100)  # pas d'écart à la théorie (60 %)
        clear = Evidence(Value(40, 1.0, 0.5, 1.5), ["ils se couchent trop"])
        found = leakcheck.chance(like, clear, hands=500)
        self.assertEqual((found.verdict, found.direction), ("opportunité", "plus"))
        self.assertAlmostEqual(found.bb100, 2.0)  # 20 occasions pour 100 mains : 2 décisions de plus à 10 points
        self.assertEqual(found.badge, "à exploiter : plus (+2,0 bb/100 par 10 pts)")
        self.assertIn("chaque tranche de 10 points rapporterait environ +2,0 bb/100", found.summary)
        raises = Reply("quand ils continuent face à ta c-bet, ils relancent", Ratio(40, 100), 0.15, sign=-1)
        self.assertIsNone(leakcheck.chance(like, clear, 500, [raises]))  # ils relancent trop : pas si sûr
        self.assertIsNone(leakcheck.chance(like, Evidence(Value(40, 0.2, -0.5, 0.9)), 500))  # rien de net
        self.assertIsNone(leakcheck.chance(stat(90, 100), clear, 500))  # un écart : c'est le verdict qui compte

    def test_weights_and_order(self):
        cheap = stat(90, 100)
        cheap.check = leakcheck.Check("", "plus", 1.2, -0.4, 2.8)  # au plus 0,4 bb/100
        self.assertAlmostEqual(leaks.importance(cheap, "reg"), 0.4)
        costly = stat(90, 100)
        costly.key, costly.label = "srp:barrel", "BTN : 2e barrel (turn)"
        costly.check = leakcheck.Check("fuite", "plus", -3.0, -4.0, -2.0, measured=True)
        kept = stat(90, 100)
        kept.key, kept.label = "srp:delayed", "BTN : C-bet retardée (turn)"
        kept.check = leakcheck.Check("exploit", "plus", 2.0, 1.0, 3.0, measured=True)
        ranked = leaks.rank([cheap, costly, kept], [], analyzed=0)
        self.assertEqual([(x.title, x.confirmed, round(x.weight, 2)) for x in ranked],
                         [("SRP · BTN : 2e barrel (turn)", True, 3.0), ("SRP · BTN : C-bet flop", False, 0.4)])
        self.assertEqual([x.title for x in leaks.kept([cheap, costly, kept])], ["SRP · BTN : C-bet retardée (turn)"])
        gaps = leaks.top_stat_gaps([kept, cheap, costly], "reg")
        self.assertEqual([s.key for s, _, _ in gaps], ["srp:barrel", "srp:cbet", "srp:delayed"])
        self.assertEqual(leaks.gap_advice(kept, "plus", "reg"), "À garder : il exploite tes adversaires")


class SolverTest(unittest.TestCase):
    def test_solver_stat(self):
        cases = {("srp", "bet:fi:", "aggressive"): "srp:cbet", ("srp", "bet:ti:i", "passive"): "srp:barrel",
                 ("srp", "bet:ri:ii", "aggressive"): "srp:barrel3", ("srp", "bet:ti:x", "aggressive"): "srp:delayed",
                 ("srp", "face:fo::1", "fold"): "srp:fold_flop", ("srp", "face:fo::1", "aggressive"): "srp:raise",
                 ("srp", "face:to:i:1", "fold"): "srp:fold_turn", ("srp", "face:fi::2", "fold"): "srp:fold_raise",
                 ("srp", "bet:to:x", "aggressive"): "srp:stab", ("3bet", "bet:fo:", "aggressive"): "3bet:cbet",
                 ("3bet", "bet:fi:", "aggressive"): "3bet:stab",
                 ("6max_bb_co_srp", "bet:fi:", "aggressive"): "srp_ip:agresseur:cbet",
                 ("6max_bb_co_srp", "face:fo::1", "fold"): "srp_ip:defenseur:fold_flop",
                 ("6max_sb_btn_3bet", "bet:fo:", "aggressive"): "3bet_oop:agresseur:cbet"}
        for (family, key, category), expected in cases.items():
            self.assertEqual(leakcheck.solver_stat(family, key, category), expected, (family, key, category))
        self.assertIsNone(leakcheck.solver_stat("srp", "face:fo::1", "passive"))
        self.assertIsNone(leakcheck.solver_stat("inconnue", "bet:fi:", "aggressive"))

    def test_solver_loss_from_an_exploit(self):
        """Il c-bet plus que le solveur, qui y voit de l'EV perdue ; mais la c-bet large exploite ses adversaires."""
        cbet = stat(90, 100)
        cbet.check = leakcheck.Check("exploit", "plus", 2.0, 1.0, 3.0, measured=True)
        group = {"family": "srp", "key": "bet:fi:", "label": "C-bet", "n": 20, "known": 20, "errors": 6, "lost": 4.0,
                 "observed": {"fold": 0, "passive": 2, "aggressive": 18},
                 "expected": {"fold": 0.0, "passive": 8.0, "aggressive": 12.0},
                 "variance": {"fold": 0.0, "passive": 4.0, "aggressive": 4.0}}
        leak = leaks._solver_leak(group, 30, [], {"srp:cbet": cbet})
        self.assertFalse(leak.confirmed)
        self.assertIn("À relativiser", leak.note)
        self.assertIn("Les écarts justifiés", leak.note)
        self.assertTrue(leaks._solver_leak(group, 30, [], {}).confirmed)
        self.assertTrue(leak.verified.endswith("une partie de ces bb n'est pas perdue."))


class HeadsUpReportTest(IsolatedHome):
    def build(self, folds):
        hands = [cbet_hand(n, folds=n < folds) for n in range(40)]
        with mock.patch("analyzer.theory.exploit.baselines", side_effect=lambda family, role: BASE[role]
                        if family == "srp" else {}):
            return leaks.build(hands, "Hero", {"Villain": {"kind": "reg"}})

    def test_cbet_that_exploits(self):
        """Elle c-bet tout ; la BB se couche 75 % au lieu de 30 % : ce n'est pas une fuite."""
        report = self.build(folds=30)
        cbet = next(s for s in report.stats if s.key == "srp:cbet")
        self.assertEqual((cbet.ratios["reg"], cbet.verdict("reg")), (Ratio(40, 40), ("plus", "solide")))
        self.assertEqual((cbet.check.verdict, cbet.check.measured), ("exploit", True))
        self.assertAlmostEqual(cbet.check.bb100, 0.45 * 6.5 * 40)  # 40 c-bets en plus pour 100 mains
        self.assertTrue(cbet.check.proofs[0].startswith(
            "Face à tes c-bets, ils se couchent 75 % (40 fois, à 30 % du pot en moyenne), contre 30 % face à la "
            "théorie"))
        self.assertEqual(len(cbet.check.proofs), 1)  # leur fold, mesuré, n'est pas répété dans leurs réponses
        self.assertNotIn("SRP · BTN : C-bet flop", [x.title for x in report.leaks])
        self.assertEqual(report.exploits[0].title, "SRP · BTN : C-bet flop")
        summary = leaks.summary(report)
        self.assertEqual(summary["ecarts_qui_rapportent"][0]["verifie_en_jeu"]["verdict"], "exploit")
        row = next(r for r in summary["stats"] if r["stat"] == "BTN : C-bet flop")
        self.assertEqual(row["en_jeu"]["bb_100"], round(0.45 * 6.5 * 40, 2))
        page = build_leaks_page(report, "/api/leaks", "/moi")
        for text in ("Les écarts justifiés", "rapporte +117,0 bb/100", "<th>En jeu</th>",
                     "À garder : il exploite tes adversaires"):
            self.assertIn(text, page)

    def test_cbet_that_leaks(self):
        """Elle c-bet tout ; la BB paie toujours : la c-bet en plus perd, la fuite passe en tête."""
        report = self.build(folds=0)
        first = report.leaks[0]
        self.assertEqual((first.title, first.confirmed, first.confidence), ("SRP · BTN : C-bet flop", True, "solide"))
        self.assertAlmostEqual(first.weight, 0.3 * 6.5 * 40)  # ce qu'elle coûte, en bb/100
        self.assertEqual(first.check.verdict, "fuite")
        self.assertEqual(report.exploits, [])
        page = build_leaks_page(report, "/api/leaks", "/moi")
        self.assertIn("Vérifié en jeu : cet écart te coûte environ 78,0 bb/100.", page)
        self.assertIn("Face à tes c-bets, ils se couchent 0 % (40 fois", page)
        self.assertNotIn("<h2>Les écarts justifiés</h2>", page)


class OpportunityReportTest(IsolatedHome):
    def test_open_wider(self):
        """Elle ouvre son bouton comme la solution (82 %) ; la BB se couche 61 % au lieu de 27 % et ne 3bet jamais :
        ouvrir plus large rapporterait."""
        hands = []
        for n in range(50):
            if n < 9:
                hands.append(hu_hand(n, [("preflop", "BTN", FOLD)], "Villain", ("7h", "2d")))
            elif n < 34:
                hands.append(hu_hand(n, [("preflop", "BTN", RAISE, 5.0), ("preflop", "BB", FOLD)], "Hero",
                                     HANDS[n % 4]))
            else:
                hands.append(cbet_hand(n, folds=n % 2 == 0))
        with mock.patch("analyzer.theory.exploit.baselines", return_value={}):
            report = leaks.build(hands, "Hero", {"Villain": {"kind": "reg"}})
        opened = next(s for s in report.stats if s.key == "sb_first.raise")
        self.assertEqual((opened.ratios["reg"], opened.verdict("reg")), (Ratio(41, 50), None))
        self.assertEqual((opened.check.verdict, opened.check.direction), ("opportunité", "plus"))
        self.assertIn("Face à tes opens, ils se couchent 61 % (41 fois), contre 27 % face à la théorie",
                      opened.check.proofs[0])
        self.assertIn("Face à tes opens, ils 3bet 0 % (41 fois), moins que le solveur (22 %).", opened.check.proofs)
        self.assertEqual([x.title for x in report.opportunities], ["Préflop · Bouton : open"])
        self.assertTrue(report.opportunities[0].advice.startswith("Relance plus large ici contre eux"))
        self.assertEqual(leaks.summary(report)["exploitations_possibles"][0]["titre"], "Préflop · Bouton : open")
        page = build_leaks_page(report, "/api/leaks", "/moi")
        self.assertIn("<h2>Les exploitations possibles</h2>", page)
        self.assertIn("à exploiter : plus", page)


class RingReportTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        ring_ranges.save_solution("6-max", {"format": "6-max", "source": "Charts de test — synthétiques",
                                            "lines": {k: {"ranges": v} for k, v in LINES.items()}})

    def test_steal_that_works(self):
        """UTG ouvre 72o, que ses charts foldent ; tout le monde se couche : l'open en plus rapporte 1,5 bb."""
        hands = [table_hand(n, "UTG", ["7h", "2d"], [("UTG", RAISE, 5.0)] + [(p, FOLD) for p in POSITIONS[1:]])
                 for n in range(30)]
        report = ring_leaks.build(hands, "Hero")
        utg = next(s for s in report.stats if s.key == "pre:open:UTG:open")
        self.assertEqual((utg.check.verdict, utg.check.measured), ("exploit", True))
        self.assertAlmostEqual(utg.check.bb100, 150.0)  # 1,5 bb × 100 opens en plus pour 100 mains
        self.assertIn("Tes opens avec des mains que la théorie folde le plus souvent : +1,50 bb par main",
                      utg.check.proofs[0])
        self.assertEqual([x.title for x in report.exploits], ["Préflop · premier à parler · UTG : open"])
        self.assertEqual(report.leaks, [])

    def test_open_that_loses(self):
        """UTG ouvre 72o, la BB paie et gagne à l'abattage : chaque open en plus perd 2,5 bb."""
        pre = [("UTG", RAISE, 5.0)] + [(p, FOLD) for p in POSITIONS[1:5]] + [("BB", CALL)]
        post = [("flop", "BB", CHECK), ("flop", "UTG", CHECK), ("turn", "BB", CHECK), ("turn", "UTG", CHECK),
                ("river", "BB", CHECK), ("river", "UTG", CHECK)]
        hands = [table_hand(n, "UTG", ["7h", "2d"], pre, post, winner="BB") for n in range(30)]
        report = ring_leaks.build(hands, "Hero")
        leak = next(x for x in report.leaks if x.title == "Préflop · premier à parler · UTG : open")
        self.assertEqual((leak.confirmed, leak.check.verdict), (True, "fuite"))
        self.assertAlmostEqual(leak.weight, 250.0)
        self.assertEqual(report.exploits, [])


if __name__ == "__main__":
    unittest.main()

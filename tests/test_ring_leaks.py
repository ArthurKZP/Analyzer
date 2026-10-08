"""Leakfinding aux tables à plusieurs : préflop face aux charts (mêmes cartes), après le flop face aux plans de jeu des
flops 6-max, pots à deux passés au solveur, rapport par format dans l'application."""
import http.client
import shutil
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from analyzer import leaks, ring_leaks
from analyzer.app.leaks_page import build_leaks_page
from analyzer.models import BET, CALL, CHECK, FOLD, POST_BB, POST_SB, RAISE, Action, Hand, Seat
from analyzer.stats import Ratio
from analyzer.theory import ring_ranges
from tests.base import IsolatedHome

SITES = Path(__file__).parent / "sites"
FIXTURES = Path(__file__).parent / "fixtures"
POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")  # sièges 1 à 6 : le bouton au siège 4

# Charts synthétiques (pas ceux d'un site) : UTG n'ouvre que les grosses paires, la BB défend face au CO.
LINES = {
    "UTG:raise BB:call": {"UTG": "AA,KK,QQ", "BB": "JJ,TT"},
    "CO:raise BB:call": {"CO": "AA,KK,QQ,AKs,72o", "BB": "JJ,TT,98s"},
    "CO:raise BB:raise CO:call": {"CO": "QQ,AKs", "BB": "AA,KK"},
}


def table_hand(n, hero_pos, cards, preflop, post=(), winner=None, board=("2c", "7d", "9s", "Jh", "3c")):
    """Une main 6-max à 1/2 € : preflop, [(position, action, montant de la relance)] ; post, [(street, position, action,
    montant de la mise)]. Le gagnant (une position) prend tout le pot."""
    names = {pos: "Hero" if pos == hero_pos else f"Joueur{k + 1}" for k, pos in enumerate(POSITIONS)}
    seats = {names[pos]: Seat(names[pos], k + 1, 200.0, is_button=pos == "BTN", is_hero=pos == hero_pos)
             for k, pos in enumerate(POSITIONS)}
    actions, state = [], {"put": dict.fromkeys(seats, 0.0), "pot": 0.0, "street": "preflop"}

    def add(street, pos, kind, value=0.0):
        if street != state["street"]:
            state["street"], state["put"] = street, dict.fromkeys(seats, 0.0)
        put, player = state["put"], names[pos]
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

    add("preflop", "SB", POST_SB, 1.0)
    add("preflop", "BB", POST_BB, 2.0)
    for step in preflop:
        add("preflop", *step)
    for step in post:
        add(*step)
    pot = round(state["pot"], 2)
    hand = Hand("Test", f"R{n:04d}", "T1", "NLHE 1/2 6 max", datetime(2026, 10, 1, 12) + timedelta(minutes=n), 1.0, 2.0,
                pot, 0.0, 6, seats, {"Hero": list(cards)}, list(board), actions, {names[winner or hero_pos]: pot})
    hand.finalize()
    return hand


FOLDS_TO = {pos: [(p, FOLD) for p in POSITIONS[:POSITIONS.index(pos)]] for pos in POSITIONS}


def co_vs_bb(n, hero_pos, cards, post, winner=None):
    """Open du CO payé par la BB (les autres foldent), puis l'après-flop donné."""
    pre = FOLDS_TO["CO"] + [("CO", RAISE, 5.0), ("BTN", FOLD), ("SB", FOLD), ("BB", CALL)]
    return table_hand(n, hero_pos, cards, pre, post, winner)


class PotTest(unittest.TestCase):
    def test_pots(self):
        srp = co_vs_bb(1, "CO", ["Ah", "Kd"], [])
        pot = ring_leaks.pot_of(srp)
        self.assertEqual((pot.kind, pot.positions, pot.structure, pot.family),
                         ("srp", ("BB", "CO"), "srp_ip", "6max_bb_co_srp"))
        self.assertEqual(pot.aggressor, "Hero")
        three = table_hand(2, "BTN", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", RAISE, 15.0), ("SB", FOLD), ("BB", FOLD), ("CO", CALL)])
        pot = ring_leaks.pot_of(three)
        self.assertEqual((pot.kind, pot.positions, pot.structure), ("3bet", ("CO", "BTN"), "3bet_ip"))
        multiway = table_hand(3, "CO", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", CALL), ("SB", FOLD), ("BB", CALL)])
        self.assertIsNone(ring_leaks.pot_of(multiway))
        # le bouton paie l'open, la BB 3bette : le CO folde, le bouton paie, mais le CO a mis de l'argent
        dead = table_hand(4, "BB", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", CALL), ("SB", FOLD), ("BB", RAISE, 20.0), ("CO", FOLD), ("BTN", CALL)])
        self.assertIsNone(ring_leaks.pot_of(dead))
        limped = table_hand(5, "BB", ["Ah", "Kd"], FOLDS_TO["SB"] + [("SB", CALL), ("BB", CHECK)])
        self.assertIsNone(ring_leaks.pot_of(limped))

    def test_postflop_events(self):
        cbet = co_vs_bb(1, "CO", ["Ah", "Kd"], [("flop", "BB", CHECK), ("flop", "CO", BET, 3.0), ("flop", "BB", FOLD)])
        defend = co_vs_bb(2, "BB", ["Ah", "Kd"], [("flop", "BB", CHECK), ("flop", "CO", BET, 3.0), ("flop", "BB", FOLD)],
                          winner="CO")
        hands = [cbet, defend]
        pots = ring_leaks._pots(hands, "Hero")
        with mock.patch("analyzer.theory.exploit.baselines", return_value={}):
            rows = {s.label: s for s in ring_leaks.postflop_stats(hands, "Hero", "6-max", pots)}
        self.assertEqual(rows["À l'initiative : C-bet flop"].ratios["all"], Ratio(1, 1))
        self.assertEqual(rows["En défense : Fold face à la c-bet"].ratios["all"], Ratio(1, 1))
        self.assertEqual(rows["À l'initiative : C-bet flop"].section, "SRP, l'ouvreur en position")
        self.assertIn("pas encore de repère du solveur", rows["En défense : Fold face à la c-bet"].note)


class RingReportTest(IsolatedHome):
    def charts(self):
        ring_ranges.save_solution("6-max", {"format": "6-max", "source": "Charts de test — synthétiques",
                                            "lines": {k: {"ranges": v} for k, v in LINES.items()}})

    def hands(self):
        out = []
        for n in range(30):  # UTG : ouvre 72o, que ses charts foldent
            out.append(table_hand(n, "UTG", ["7h", "2d"], [("UTG", RAISE, 5.0)] + [(p, FOLD) for p in POSITIONS[1:]]))
        for n in range(30, 55):  # le CO ouvre, la BB paie : c-bet à chaque fois
            out.append(co_vs_bb(n, "CO", ["Ah", "Kd"], [("flop", "BB", CHECK), ("flop", "CO", BET, 3.0),
                                                        ("flop", "BB", FOLD)]))
        return out

    def test_preflop_against_charts(self):
        self.charts()
        rows = {s.label: s for s in ring_leaks.stats(self.hands(), "Hero", "6-max")}
        utg = rows["UTG : open"]
        self.assertEqual((utg.ratios["all"], utg.reference, utg.exact), (Ratio(30, 30), 0.0, True))
        self.assertEqual(utg.verdict("all"), ("plus", "solide"))
        self.assertEqual(utg.section, "Préflop · premier à parler")
        self.assertEqual(utg.link[0], "mains#fmt=ring&pos=UTG&sit=open")
        leak = leaks._stat_leak(utg, "all")
        self.assertEqual(leak.evidence, "100 % sur 30 occasions, contre 0 % pour ses charts avec les mêmes cartes")
        self.assertEqual(leak.advice, "Tu ouvres trop large à cette position : resserre.")
        # le CO ouvre AKo, hors de ses charts (AKs seulement) : open à 100 %, les charts à 0 %
        self.assertEqual(rows["CO : open"].reference, 0.0)

    def test_without_charts(self):
        rows = {s.label: s for s in ring_leaks.stats(self.hands(), "Hero", "6-max")}
        utg = rows["UTG : open"]  # repère indicatif d'un régulier 6-max (ring.OPEN_6MAX)
        self.assertEqual((utg.reference, utg.band), (None, (0.14, 0.18)))
        self.assertEqual(utg.verdict("all"), ("plus", "solide"))
        self.assertIn("charge tes charts", utg.note)

    def test_postflop_against_plans(self):
        plans = {"cbet": (0.5, 8), "barrel": (0.5, 8)}
        with mock.patch("analyzer.theory.exploit.baselines",
                        side_effect=lambda family, role: plans if role == "agresseur" else {}):
            rows = {s.label: s for s in ring_leaks.stats(self.hands(), "Hero", "6-max")}
        cbet = rows["À l'initiative : C-bet flop"]
        self.assertEqual((cbet.ratios["all"], cbet.reference, cbet.fragile), (Ratio(25, 25), 0.5, False))
        self.assertEqual(cbet.verdict("all"), ("plus", "solide"))
        self.assertEqual(cbet.stake, 2.0)  # le flop, dans un pot simple
        self.assertEqual(cbet.link, ("plan#famille=6max_bb_btn_srp", "Voir le plan de jeu 6-max"))
        # pas de repère du solveur en 3-max (les spots d'étude sont en 6-max)
        self.assertEqual(ring_leaks.stats(self.hands(), "Hero", "3-max"), [])

    def test_report_and_page(self):
        self.charts()
        report = ring_leaks.build(self.hands(), "Hero", "6-max")
        self.assertEqual((report.table_format, report.solver_scope, report.scopes, report.hands),
                         ("6-max", "all", ring_leaks.SCOPES, 55))
        self.assertEqual((report.context["charts"], report.context["pots"]), (True, 25))
        self.assertEqual(report.leaks[0].title, "Préflop · premier à parler · UTG : open")
        picks = report.picks["all"]
        self.assertEqual(len(picks), 2)  # deux par ligne : SRP · CO c. BB · flop
        self.assertEqual(picks[0].line, "SRP · CO c. BB · flop")
        self.assertIsNotNone(picks[0].spot)  # la ligne a ses ranges : la main se résout
        self.assertEqual(leaks.selection_spots(report), [p.spot for p in picks])
        summary = leaks.summary(report)  # pour le coach
        self.assertEqual((summary["format"], summary["mains_par_type"], summary["leaks"][0]["titre"]),
                         ("6-max", {"all": 55}, "Préflop · premier à parler · UTG : open"))
        self.assertEqual(summary["stats"][0]["ecart"], "plus/solide")
        page = build_leaks_page(report, "/api/leaks", "/moi", formats=[("HU", 4), ("6-max", 55)])
        for text in ("Leakfinding 6-max de Hero", 'aria-current="page">6-max', 'href="?format=HU"',
                     "Les écarts les plus importants", "Le détail : toutes ses stats", "Préflop · premier à parler",
                     "Face au solveur, ses pots à deux au flop", 'data-query="?format=6-max"',
                     "rapport?format=6-max", "/explorateur/R0030", "Pas encore de flop 6-max résolu"):
            self.assertIn(text, page)
        self.assertLess(page.index("Les écarts les plus importants"), page.index("Le détail : toutes ses stats"))
        self.assertNotIn("Contre les récréatifs", page)
        alone = build_leaks_page(report, "/api/leaks", "/moi", embed=False, standalone=True,
                                 formats=[("HU", 4), ("6-max", 55)])
        self.assertNotIn('class="lk-fmt"', alone)
        self.assertNotIn("Revoir ↗", alone)
        self.assertNotIn("Études du solveur ›", alone)

    def test_solver_digest_named_with_positions(self):
        hand = table_hand(1, "BB", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", FOLD), ("SB", RAISE, 20.0), ("BB", FOLD), ("CO", CALL)])
        pot = ring_leaks.pot_of(hand)
        self.assertEqual((pot.positions, pot.structure, pot.family), (("SB", "CO"), "3bet_oop", "6max_sb_co_3bet"))
        digest = {"hand": hand.hand_id, "family": "3bet", "hero_position": "BB",
                  "decisions": [{"who": "H", "key": "bet:fi:", "label": "Stab flop du BTN"}]}
        named = ring_leaks._relabel(digest, hand, pot, "Joueur5")
        self.assertEqual((named["family"], named["hero_position"]), ("6max_sb_co_3bet", "SB"))
        self.assertEqual(named["decisions"][0]["label"], "Stab flop du CO")  # nommée avec les vraies positions
        self.assertEqual(digest["decisions"][0]["label"], "Stab flop du BTN")  # le résumé gardé ne change pas
        self.assertEqual(leaks.family_name("6max_sb_co_3bet"), "pot 3bet SB c. CO")


class AppTest(IsolatedHome):
    """Un rapport par format de table dans l'application : heads-up, 6-max, 3-max."""

    def setUp(self):
        super().setUp()
        self.folder = self.home.parent / "mains"
        self.folder.mkdir()
        shutil.copy(FIXTURES / "betclic_sample.txt", self.folder / "hu.txt")
        for name in ("betclic_6max.txt", "unibet.txt", "winamax.txt"):
            shutil.copy(SITES / name, self.folder / name)
        from analyzer.app.library import Library
        from analyzer.app.server import start
        self.lib = Library(self.folder)
        self.server = start(self.lib, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=30)
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, resp.getheader("Content-Disposition", ""), data.decode()

    def test_formats(self):
        self.assertEqual([fmt for fmt, _ in self.lib.leak_formats()], ["HU", "6-max", "3-max"])
        status, _, page = self.request("GET", "/moi/leaks")  # le heads-up d'abord
        self.assertEqual(status, 200)
        self.assertIn("Leakfinding de Hero", page)
        self.assertIn('href="?format=6-max"', page)
        status, _, page = self.request("GET", "/moi/leaks?format=6-max")
        self.assertEqual(status, 200)
        self.assertIn("Leakfinding 6-max de Hero", page)
        self.assertEqual(self.request("GET", "/moi/leaks?format=3-max")[0], 200)
        self.assertEqual(self.request("GET", "/moi/leaks?format=9-max")[0], 404)
        status, disposition, _ = self.request("GET", "/moi/rapport?format=6-max")
        self.assertEqual((status, disposition), (200, 'attachment; filename="leakfinding-6-max.html"'))
        status, _, body = self.request("GET", "/api/leaks?format=6-max")
        self.assertEqual(status, 200)
        self.assertIn('"total"', body)
        self.assertEqual(self.request("POST", "/api/leaks/arreter?format=6-max", "{}")[0], 200)
        self.assertEqual(self.request("POST", "/api/leaks/lancer?format=9-max", "{}")[0], 404)
        status, _, page = self.request("GET", "/moi/tables")  # les écarts en tête de la page des tables
        self.assertEqual(status, 200)
        self.assertIn("Tes écarts les plus importants", page)
        self.assertIn('href="leaks?format=6-max"', page)
        self.assertEqual(self.lib.summary()["ring_hands"], 3)


class RingOnlyTest(IsolatedHome):
    def test_only_six_max_hands(self):
        """Un joueur (ou un élève) qui ne joue qu'aux tables à plusieurs : son Leakfinding s'ouvre sur son format, et
        les pages du heads-up le disent au lieu d'échouer."""
        folder = self.home.parent / "mains"
        folder.mkdir()
        for name in ("betclic_6max.txt", "unibet.txt"):
            shutil.copy(SITES / name, folder / name)
        from analyzer.app.library import Library
        lib = Library(folder)
        self.assertEqual((len(lib.hands), lib.summary()["ring_hands"]), (0, 2))
        self.assertEqual([fmt for fmt, _ in lib.leak_formats()], ["6-max"])
        self.assertIn("Leakfinding 6-max de Hero", lib.self_page("leaks"))
        self.assertIn("Aucune main heads-up", lib.self_page("bilan"))
        self.assertIn("Aucune main heads-up", lib.self_page("solveur"))
        self.assertIn("Tes écarts les plus importants", lib.self_page("tables"))


if __name__ == "__main__":
    unittest.main()

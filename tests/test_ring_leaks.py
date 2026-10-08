"""Leakfinding aux tables à plusieurs, toutes ensemble (3 à 9 joueurs) : préflop face aux charts (mêmes cartes, ceux
du 6-max à même nombre de joueurs derrière), après le flop face aux plans de jeu des flops 6-max, pots à deux passés au
solveur, sur toutes ses mains, contre les réguliers et contre les récréatifs ; un rapport dans l'application."""
import http.client
import shutil
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from analyzer import leaks, players, ring, ring_leaks
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
    "BTN:raise BB:call": {"BTN": "AA,KK,QQ,JJ,AKs", "BB": "TT,99"},
    "CO:raise BB:call": {"CO": "AA,KK,QQ,AKs,72o", "BB": "JJ,TT,98s"},
    "CO:raise BB:raise CO:call": {"CO": "QQ,AKs", "BB": "AA,KK"},
}
REC = {"Joueur6": {"kind": "rec"}}  # la BB des mains de test : un récréatif


def table_hand(n, hero_pos, cards, preflop, post=(), winner=None, board=("2c", "7d", "9s", "Jh", "3c"),
               positions=POSITIONS):
    """Une main 6-max (ou aux positions données) à 1/2 € : preflop, [(position, action, montant de la relance)] ; post,
    [(street, position, action, montant de la mise)]. Le gagnant (une position) prend tout le pot."""
    names = {pos: "Hero" if pos == hero_pos else f"Joueur{k + 1}" for k, pos in enumerate(positions)}
    seats = {names[pos]: Seat(names[pos], k + 1, 200.0, is_button=pos == "BTN", is_hero=pos == hero_pos)
             for k, pos in enumerate(positions)}
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
                pot, 0.0, len(positions), seats, {"Hero": list(cards)}, list(board), actions, {names[winner or hero_pos]: pot})
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
            rows = {s.label: s for s in ring_leaks.postflop_stats(hands, "Hero", pots, {cbet.hand_id: "rec"})}
        cbets = rows["À l'initiative : C-bet flop"]
        self.assertEqual((cbets.ratios["all"], cbets.ratios["rec"], cbets.ratios["reg"]),
                         (Ratio(1, 1), Ratio(1, 1), Ratio(0, 0)))
        folds = rows["En défense : Fold face à la c-bet"]
        self.assertEqual((folds.ratios["all"], folds.ratios["reg"]), (Ratio(1, 1), Ratio(1, 1)))
        self.assertEqual(cbets.section, "SRP, l'ouvreur en position")
        self.assertIn("pas encore de repère du solveur", folds.note)


class VersusTest(unittest.TestCase):
    """Une main compte contre les récréatifs quand un récréatif a mis de l'argent dans le pot pendant que le joueur y
    était encore."""

    def test_versus(self):
        called = co_vs_bb(1, "CO", ["Ah", "Kd"], [])  # la BB (Joueur6) paie son open
        self.assertEqual(ring_leaks.versus(called, "Hero", REC), "rec")
        self.assertEqual(ring_leaks.versus(called, "Hero", {}), "reg")
        self.assertEqual(ring_leaks.versus(called, "Hero", {"Joueur6": {"kind": "reg"}}), "reg")
        folded = table_hand(2, "CO", ["Ah", "Kd"], FOLDS_TO["CO"] + [("CO", RAISE, 5.0), ("BTN", FOLD), ("SB", FOLD),
                                                                       ("BB", FOLD)])
        self.assertEqual(ring_leaks.versus(folded, "Hero", REC), "reg")  # sa grosse blinde seulement
        gone = table_hand(3, "UTG", ["7h", "2d"], [("UTG", FOLD)] + [(p, FOLD) for p in ("HJ", "CO", "BTN")] + [
            ("SB", CALL), ("BB", CHECK)])  # le joueur s'est couché avant que le récréatif ne joue
        self.assertEqual(ring_leaks.versus(gone, "Hero", REC), "reg")
        parts = ring_leaks.split([called, folded, gone], "Hero", REC)
        self.assertEqual({k: [h.hand_id for h in v] for k, v in parts.items()},
                         {"all": ["R0001", "R0002", "R0003"], "reg": ["R0002", "R0003"], "rec": ["R0001"]})


class ChartsTest(IsolatedHome):
    """Une table sans charts à elle prend ceux du 6-max, à même nombre de joueurs derrière."""

    def test_mapping(self):
        formats = {"6-max": 3}
        self.assertEqual(ring_ranges.chart_mapping("6-max", formats), ("6-max", {}))
        self.assertEqual(ring_ranges.chart_mapping("3-max", formats)[1]["BTN"], "BTN")
        fmt, mapping = ring_ranges.chart_mapping("9 joueurs", formats)
        self.assertEqual((fmt, mapping["LJ"], mapping["CO"], mapping.get("UTG")), ("6-max", "UTG", "CO", None))
        self.assertEqual(ring_ranges.chart_mapping("3-max", {"3-max": 1, "6-max": 2}), ("3-max", {}))
        self.assertIsNone(ring_ranges.chart_mapping("6-max", {}))
        self.assertIsNone(ring_ranges.chart_mapping("HU", formats))

    def test_resolve(self):
        ring_ranges.save_solution("6-max", {"lines": {k: {"ranges": v} for k, v in LINES.items()}})
        self.assertEqual(ring_ranges.resolve("9 joueurs", [("LJ", "raise"), ("BB", "call")]),
                         ("6-max", [("UTG", "raise"), ("BB", "call")]))
        self.assertIsNone(ring_ranges.resolve("9 joueurs", [("UTG+1", "raise"), ("BB", "call")]))
        pot_type, ranges = ring_ranges.lookup(*ring_ranges.resolve("3-max", [("CO", "raise"), ("BB", "call")]))
        self.assertEqual((pot_type, sorted(ranges)), ("SRP", ["BB", "CO"]))
        self.assertIn("charts 6-max", ring_ranges.missing_hint("9 joueurs"))


class OpponentsTest(IsolatedHome):
    def test_suggestion_at_ring_tables(self):
        """Trop de mains jouées, de limps et de calls : un récréatif, aux normes de la taille de table où il a joué."""
        loose = {"6": {"hands": 60, "vpip": Ratio(40, 60), "pfr": Ratio(5, 60), "limp": Ratio(6, 20),
                       "flat": Ratio(10, 20), "fold_cbet": Ratio(8, 20)}}
        kind, reasons = players.ring_suggest(loose)
        self.assertEqual(kind, "rec")
        self.assertIn("joue 67 % de ses mains", reasons)
        solid = {"6": {"hands": 60, "vpip": Ratio(14, 60), "pfr": Ratio(11, 60), "limp": Ratio(0, 20),
                       "flat": Ratio(3, 20), "fold_cbet": Ratio(9, 20)},
                 "3": {"hands": 5, "vpip": Ratio(5, 5)}}  # trop peu de mains à 3 : la table à 6 décide
        self.assertEqual(players.ring_suggest(solid), ("reg", []))
        self.assertEqual(players.ring_suggest({"6": {"hands": 10, "vpip": Ratio(10, 10)}}), (None, []))
        # à 3 joueurs, jouer 50 % de ses mains reste normal
        self.assertEqual(players.ring_suggest({"3": {"hands": 60, "vpip": Ratio(30, 60), "pfr": Ratio(24, 60)}}),
                         ("reg", []))
        found = players.classify(["Joueur6"], {}, ring_profiles={"Joueur6": loose})
        self.assertEqual((found["Joueur6"]["kind"], found["Joueur6"]["source"]), ("rec", "suggestion"))
        players.set_kind("Joueur6", "reg")  # ton choix l'emporte, le même qu'en heads-up
        self.assertEqual(players.classify(["Joueur6"], {}, ring_profiles={"Joueur6": loose})["Joueur6"]["kind"], "reg")

    def test_opponents_and_profiles(self):
        hands = [co_vs_bb(n, "CO", ["Ah", "Kd"], [("flop", "BB", CHECK), ("flop", "CO", BET, 3.0), ("flop", "BB", FOLD)])
                 for n in range(3)]
        hands.append(table_hand(3, "UTG", ["7h", "2d"], [("UTG", FOLD)] + [(p, FOLD) for p in ("HJ", "CO", "BTN")] + [
            ("SB", CALL), ("BB", CHECK)]))
        rows = {o["name"]: o for o in ring.opponents(hands, "Hero")}
        self.assertEqual((rows["Joueur6"]["hands"], rows["Joueur6"]["pots"]), (4, 3))
        self.assertEqual(rows["Joueur6"]["net_bb"], 9.0)  # 3 bb par pot : le call de la BB et la petite blinde
        self.assertEqual(rows["Joueur1"]["pots"], 0)
        profile = ring.profiles(hands, {"Joueur6"})["Joueur6"]["6"]
        self.assertEqual((profile["hands"], profile["vpip"]), (4, Ratio(3, 4)))


class RingReportTest(IsolatedHome):
    def charts(self):
        ring_ranges.save_solution("6-max", {"format": "6-max", "source": "Charts de test — synthétiques",
                                            "lines": {k: {"ranges": v} for k, v in LINES.items()}})

    def hands(self):
        out = []
        for n in range(30):  # UTG : ouvre 72o, que ses charts foldent ; tout le monde se couche
            out.append(table_hand(n, "UTG", ["7h", "2d"], [("UTG", RAISE, 5.0)] + [(p, FOLD) for p in POSITIONS[1:]]))
        for n in range(30, 55):  # le CO ouvre, la BB (Joueur6) paie : c-bet à chaque fois
            out.append(co_vs_bb(n, "CO", ["Ah", "Kd"], [("flop", "BB", CHECK), ("flop", "CO", BET, 3.0),
                                                        ("flop", "BB", FOLD)]))
        return out

    def test_preflop_against_charts(self):
        self.charts()
        rows = {s.label: s for s in ring_leaks.stats(self.hands(), "Hero")}
        utg = rows["UTG : open"]
        self.assertEqual((utg.ratios["all"], utg.ratios["reg"], utg.reference, utg.exact),
                         (Ratio(30, 30), Ratio(30, 30), 0.0, True))
        self.assertEqual(utg.verdict("reg"), ("plus", "solide"))
        self.assertEqual(utg.section, "Préflop · premier à parler")
        self.assertEqual(utg.link[0], "mains#fmt=ring&kind=reg&pos=UTG&sit=open")
        leak = leaks._stat_leak(utg, "reg")
        self.assertEqual(leak.evidence, "100 % sur 30 occasions contre les réguliers, contre 0 % pour les charts avec les "
                                        "mêmes cartes")
        self.assertEqual(leak.advice, "Tu ouvres trop large à cette position : resserre.")
        # le CO ouvre AKo, hors des charts (AKs seulement) : open à 100 %, les charts à 0 %
        self.assertEqual(rows["CO : open"].reference, 0.0)

    def test_regulars_and_recreationals(self):
        """La BB qui paie l'open du CO est un récréatif : ces mains comptent contre les récréatifs, à part."""
        self.charts()
        rows = {s.label: s for s in ring_leaks.stats(self.hands(), "Hero", REC)}
        co = rows["CO : open"]
        self.assertEqual((co.ratios["all"], co.ratios["reg"], co.ratios["rec"]), (Ratio(25, 25), Ratio(0, 0),
                                                                                  Ratio(25, 25)))
        self.assertIsNone(co.verdict("reg"))  # rien contre les réguliers : pas de leak
        self.assertEqual(co.verdict("rec"), ("plus", "solide"))  # l'écart reste visible contre les récréatifs
        self.assertEqual(rows["UTG : open"].ratios["reg"], Ratio(30, 30))

    def test_without_charts(self):
        rows = {s.label: s for s in ring_leaks.stats(self.hands(), "Hero")}
        utg = rows["UTG : open"]  # repère indicatif d'un régulier 6-max (ring.OPEN_6MAX)
        self.assertEqual((utg.reference, utg.band), (None, (0.14, 0.18)))
        self.assertEqual(utg.verdict("reg"), ("plus", "solide"))
        self.assertIn("charge tes charts", utg.note)

    def test_postflop_against_plans(self):
        plans = {"cbet": (0.5, 8), "barrel": (0.5, 8)}
        with mock.patch("analyzer.theory.exploit.baselines",
                        side_effect=lambda family, role: plans if role == "agresseur" else {}):
            rows = {s.label: s for s in ring_leaks.stats(self.hands(), "Hero")}
        cbet = rows["À l'initiative : C-bet flop"]
        self.assertEqual((cbet.ratios["reg"], cbet.reference, cbet.fragile), (Ratio(25, 25), 0.5, False))
        self.assertEqual(cbet.verdict("reg"), ("plus", "solide"))
        self.assertEqual(cbet.stake, 2.0)  # le flop, dans un pot simple
        self.assertEqual(cbet.link, ("plan#famille=6max_bb_btn_srp", "Voir le plan de jeu 6-max"))

    def test_all_table_sizes_together(self):
        """Une table à 3 joueurs (BTN, SB, BB) se lit avec celles à 6 : le même rapport, les charts du 6-max."""
        self.charts()
        three = [table_hand(n, "BTN", ["7h", "2d"], [("BTN", RAISE, 5.0), ("SB", FOLD), ("BB", FOLD)],
                            positions=("BTN", "SB", "BB")) for n in range(60, 80)]  # le bouton ouvre 72o
        self.assertEqual(three[0].table_format, "3-max")
        report = ring_leaks.build(self.hands() + three, "Hero")
        self.assertEqual((report.hands, report.context["formats"]), (75, ["3-max", "6-max"]))
        button = next(s for s in report.stats if s.label == "BTN : open")  # le bouton du 3-max, aux charts du 6-max
        self.assertEqual((button.ratios["reg"], button.reference), (Ratio(20, 20), 0.0))

    def test_report_and_page(self):
        self.charts()
        report = ring_leaks.build(self.hands(), "Hero")
        self.assertEqual((report.table_format, report.solver_scope, report.scopes, report.hands),
                         ("ring", "reg", leaks.SCOPES, 55))
        self.assertEqual((report.context["charts"], report.context["pots"]), (True, 25))
        self.assertEqual(report.leaks[0].title, "Préflop · premier à parler · UTG : open")
        picks = report.picks["reg"]
        self.assertEqual(len(picks), 2)  # deux par ligne : SRP · CO c. BB · flop
        self.assertEqual(picks[0].line, "SRP · CO c. BB · flop")
        self.assertIsNotNone(picks[0].spot)  # la ligne a ses ranges : la main se résout
        self.assertEqual(report.picks["rec"], [])
        self.assertEqual(leaks.selection_spots(report), [p.spot for p in picks])
        summary = leaks.summary(report)  # pour le coach
        self.assertEqual((summary["format"], summary["mains_par_type"], summary["leaks"][0]["titre"]),
                         ("ring", {"all": 55, "reg": 55, "rec": 0}, "Préflop · premier à parler · UTG : open"))
        self.assertEqual(summary["stats"][0]["ecart_reguliers"], "plus/solide")
        page = build_leaks_page(report, "/api/leaks", "/moi", formats=[("HU", 4), ("ring", 55)])
        for text in ("Leakfinding des tables à plusieurs de Hero (6-max)", 'aria-current="page">Tables à plusieurs',
                     'href="?format=HU"', "Les écarts les plus importants", "Le détail : toutes ses stats",
                     "Préflop · premier à parler", "Face au solveur, contre les réguliers", 'data-query="?format=ring"',
                     "rapport?format=ring", "/explorateur/R0030", "Pas encore de flop 6-max résolu",
                     "Contre les récréatifs", "Toutes ses mains"):
            self.assertIn(text, page)
        self.assertLess(page.index("Les écarts les plus importants"), page.index("Le détail : toutes ses stats"))
        alone = build_leaks_page(report, "/api/leaks", "/moi", embed=False, standalone=True,
                                 formats=[("HU", 4), ("ring", 55)])
        self.assertNotIn('class="lk-fmt"', alone)
        self.assertNotIn("Revoir ↗", alone)
        self.assertNotIn("Études du solveur ›", alone)

    def test_recreational_hands_kept_out_of_the_solver(self):
        self.charts()
        report = ring_leaks.build(self.hands(), "Hero", REC)
        self.assertEqual(report.scope_hands, {"all": 55, "reg": 30, "rec": 25})
        self.assertEqual((report.picks["reg"], len(report.picks["rec"])), ([], 2))
        self.assertIsNone(report.picks["rec"][0].spot)  # à revoir à la main
        self.assertEqual(report.opponents["rec"], ["Joueur6"])
        page = build_leaks_page(report, "/api/leaks", "/moi", formats=[("ring", 55)])
        self.assertIn("à revoir à la main", page)

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
    """Deux rapports dans l'application : le heads-up, et les tables à plusieurs (3-max et 6-max ensemble)."""

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
        self.assertEqual(self.lib.leak_formats()[1:], [("ring", 3)])
        status, _, page = self.request("GET", "/moi/leaks")  # le heads-up d'abord
        self.assertEqual(status, 200)
        self.assertIn("Leakfinding de Hero", page)
        self.assertIn('href="?format=ring"', page)
        status, _, page = self.request("GET", "/moi/leaks?format=ring")
        self.assertEqual(status, 200)
        self.assertIn("Leakfinding des tables à plusieurs de Hero (3-max, 6-max)", page)
        self.assertIn("Mains ensemble", page)  # ses adversaires des tables à plusieurs, et leur type
        for old in ("6-max", "3-max", "9-max"):  # les anciennes adresses mènent au rapport commun
            self.assertEqual(self.request("GET", f"/moi/leaks?format={old}")[0], 200)
        status, disposition, _ = self.request("GET", "/moi/rapport?format=ring")
        self.assertEqual((status, disposition), (200, 'attachment; filename="leakfinding-tables-a-plusieurs.html"'))
        status, _, body = self.request("GET", "/api/leaks?format=ring")
        self.assertEqual(status, 200)
        self.assertIn('"total"', body)
        self.assertEqual(self.request("POST", "/api/leaks/arreter?format=ring", "{}")[0], 200)
        status, _, page = self.request("GET", "/moi/tables")  # les écarts en tête de la page des tables
        self.assertEqual(status, 200)
        self.assertIn("Tes écarts les plus importants", page)
        self.assertIn('href="leaks?format=ring"', page)
        self.assertEqual(self.lib.summary()["ring_hands"], 3)

    def test_ring_opponent_kind(self):
        """Le type d'un adversaire des tables à plusieurs se règle comme en heads-up, et change le découpage."""
        names = [o["name"] for o in self.lib.ring_opponents_view()]
        self.assertIn("Villain", names)
        self.assertEqual(self.lib.ring_kinds()["Villain"]["kind"], "reg")
        before = self.lib.leaks_report("ring").scope_hands
        self.lib.set_kind("Villain", "rec")
        self.assertEqual(self.lib.ring_kinds()["Villain"]["source"], "toi")
        after = self.lib.leaks_report("ring").scope_hands
        self.assertEqual(after["rec"], before["rec"] + 1)  # la main Unibet, où Villain a payé
        self.assertIn('href="leaks?format=ring"', self.lib.self_page("tables"))


class RingOnlyTest(IsolatedHome):
    def test_only_ring_hands(self):
        """Un joueur (ou un élève) qui ne joue qu'aux tables à plusieurs : son Leakfinding s'ouvre sur elles, et les
        pages du heads-up le disent au lieu d'échouer."""
        folder = self.home.parent / "mains"
        folder.mkdir()
        for name in ("betclic_6max.txt", "unibet.txt"):
            shutil.copy(SITES / name, folder / name)
        from analyzer.app.library import Library
        lib = Library(folder)
        self.assertEqual((len(lib.hands), lib.summary()["ring_hands"]), (0, 2))
        self.assertEqual(lib.leak_formats(), [("ring", 2)])
        self.assertIn("Leakfinding des tables à plusieurs de Hero", lib.self_page("leaks"))
        self.assertIn("Aucune main heads-up", lib.self_page("bilan"))
        self.assertIn("Aucune main heads-up", lib.self_page("solveur"))
        self.assertIn("Tes écarts les plus importants", lib.self_page("tables"))


if __name__ == "__main__":
    unittest.main()

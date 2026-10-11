"""Étude du field : le leakfinding des adversaires (leaks à exploiter, value et bluff par ligne, style des
récréatifs), et les Paramètres (pseudo, formats joués, coach, seuil)."""
import http.client
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path

from analyzer import field, players, settings
from analyzer.app.library import Library
from analyzer.app.server import start
from analyzer.models import BET, CALL, CHECK, FOLD, RAISE
from analyzer.stats import Ratio
from tests.base import IsolatedHome
from tests.test_ring_leaks import FOLDS_TO, table_hand

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"
CO, BTN, SB, BB = "Joueur3", "Joueur4", "Joueur5", "Joueur6"  # les places des mains de test (Hero : UTG)


def srp(n, post, board=("Kc", "7d", "2s", "Jh", "3c"), shown=None, winner=None):
    """Open du CO payé par la BB (Hero, UTG, et les autres foldent), puis l'après-flop ; shown : les cartes montrées
    à l'abattage ({joueur: cartes})."""
    pre = FOLDS_TO["CO"] + [("CO", RAISE, 5.0), ("BTN", FOLD), ("SB", FOLD), ("BB", CALL)]
    hand = table_hand(n, "UTG", ["2h", "3d"], pre, post, winner or "CO", board=board)
    if shown:
        hand.showdown = True
        hand.hole_cards.update(shown)
    return hand


def barrel_hand(n, cards, think_turn=None):
    """Le CO mise le flop et la turn (2e barrel de 12 dans un pot de 22), la BB paie jusqu'à l'abattage."""
    post = [("flop", "BB", CHECK), ("flop", "CO", BET, 6.0), ("flop", "BB", CALL),
            ("turn", "BB", CHECK), ("turn", "CO", BET, 12.0), ("turn", "BB", CALL),
            ("river", "BB", CHECK), ("river", "CO", CHECK)]
    return srp(n, post, shown={CO: cards, BB: ["Ad", "Kd"]})


class IntentAndLinesTest(unittest.TestCase):
    def test_intent(self):
        board = ["Kc", "7d", "2s"]
        self.assertEqual(field.intent_of(["Kh", "Qs"], board, "flop")[0], "value")    # top paire
        self.assertEqual(field.intent_of(["7h", "6s"], board, "flop")[0], "thin")     # 2e paire
        self.assertEqual(field.intent_of(["Qd", "Jd"], board + ["9d"], "turn")[0], "semi")  # tirage couleur
        self.assertEqual(field.intent_of(["Qd", "Jd"], board + ["9d", "4h"], "river")[0], "bluff")  # tirage manqué
        self.assertEqual(field.intent_of(["5h", "4h"], ["Kc", "Qd", "9s"], "flop")[0], "bluff")

    def test_lines_multiway(self):
        hand = srp(1, [("flop", "BB", CHECK), ("flop", "CO", BET, 6.0), ("flop", "BB", RAISE, 18.0),
                       ("flop", "CO", CALL), ("turn", "BB", BET, 20.0), ("turn", "CO", FOLD)])
        bets = field.player_bets([hand], [CO, BB])
        self.assertEqual([(b.player, b.street, b.label) for b in bets],
                         [(CO, "flop", "C-bet"), (BB, "flop", "Check-raise"), (BB, "turn", "Mise après relance au flop")])
        self.assertEqual(bets[0].size, "petite (≤ 55 %)")
        self.assertAlmostEqual(bets[0].pct, 100 * 6 / 11)  # 6 dans un pot de 11 (blindes, open, call)
        donk = srp(2, [("flop", "BB", BET, 4.0), ("flop", "CO", CALL)])
        self.assertEqual(field.player_bets([donk], [BB])[0].label, "Donk (avant l'agresseur)")
        stab = srp(3, [("flop", "BB", CHECK), ("flop", "CO", CHECK), ("turn", "BB", BET, 8.0)])
        self.assertEqual(field.player_bets([stab], [BB])[0].label, "Mise après check au flop")

    def test_postflop_read(self):
        hand = srp(1, [("flop", "BB", CHECK), ("flop", "CO", BET, 6.0), ("flop", "BB", CALL),
                       ("turn", "BB", CHECK), ("turn", "CO", BET, 12.0), ("turn", "BB", FOLD)])
        self.assertEqual(field.postflop_read(hand, CO), {"cbet_turn": True})
        self.assertEqual(field.postflop_read(hand, BB), {"xr_flop": False, "fold_turn": True})
        checked = srp(2, [("flop", "BB", CHECK), ("flop", "CO", CHECK), ("turn", "BB", BET, 8.0), ("turn", "CO", CALL),
                          ("river", "BB", BET, 16.0), ("river", "CO", FOLD)])
        self.assertEqual(field.postflop_read(checked, BB), {})  # la BB parle avant l'agresseur : pas de « mise après »
        self.assertEqual(field.postflop_read(checked, CO), {"fold_turn": False, "fold_river": True})
        stab = srp(3, [("flop", "BB", CHECK), ("flop", "CO", CHECK)])
        late = table_hand(4, "UTG", ["2h", "3d"], FOLDS_TO["CO"] + [("CO", RAISE, 5.0), ("BTN", CALL), ("SB", FOLD),
                                                                     ("BB", FOLD)],
                          [("flop", "CO", CHECK), ("flop", "BTN", BET, 6.0), ("flop", "CO", FOLD)])
        self.assertEqual(field.postflop_read(late, BTN), {"stab_flop": True})  # le CO (agresseur) checke, le BTN mise
        self.assertEqual(field.postflop_read(stab, CO), {})
        ratios = field.ring_ratios([hand, checked], [CO])
        self.assertEqual((ratios["cbet_turn"].hits, ratios["fold_bet"].opps), (1, 2))  # face aux mises : turn et river
        self.assertEqual((ratios["afq"].hits, ratios["afq"].opps), (2, 4))  # 2 mises, 1 call, 1 fold

    def test_preflop_line(self):
        hand = srp(1, [])
        self.assertEqual(field.preflop_line(hand, CO), "Open")
        self.assertEqual(field.preflop_line(hand, BB), "Call d'une relance")
        self.assertIsNone(field.preflop_line(hand, BTN))
        three = table_hand(2, "UTG", ["2h", "3d"], FOLDS_TO["CO"] + [("CO", RAISE, 5.0), ("BTN", RAISE, 15.0), ("SB", FOLD),
                                                                      ("BB", FOLD), ("CO", CALL)])
        self.assertEqual(field.preflop_line(three, CO), "Open, puis call du 3bet")
        self.assertEqual(field.preflop_line(three, BTN), "3bet")


class VerdictAndTellsTest(unittest.TestCase):
    def test_turn_barrel(self):
        hands = [barrel_hand(k, ["8c", "6c"], think_turn=None) for k in range(4)]  # rien : des bluffs
        hands += [barrel_hand(10 + k, ["Kh", "Qh"]) for k in range(2)]          # top paire : value
        bets = field.player_bets(hands, [CO])
        turn = next(ln for ln in field.lines_of(bets) if ln.street == "turn")
        self.assertEqual((turn.label, turn.count, dict(turn.intents)), ("Barrel (a misé le flop)", 6,
                                                                         {"bluff": 4, "value": 2}))
        v = field.verdict(turn)
        self.assertEqual(v.kind, "plus")
        self.assertIn("4/6", v.text)

    def test_river_verdict_and_tells(self):
        line = field.Line("river", "Barrel (a misé le turn)", "grosse (56–120 %)")
        mk = lambda intent, pct, think, card: field.Bet(None, 0, "V", "river", line.label, line.size, pct, think,  # noqa
                                                          card, False, intent)
        line.bets = [mk("value", 100.0, 3.0, "brick") for _ in range(8)] + [mk("bluff", 40.0, 12.0, "over")
                                                                             for _ in range(2)]
        v = field.verdict(line)
        self.assertEqual(v.kind, "")  # 2 bluffs sur 10, il en faut 33 % : pas encore tranché
        tells = field.tells(line.bets)
        self.assertTrue(any(t.startswith("Taille : plus grosse pour la value (100 %") for t in tells), tells)
        self.assertTrue(any(t.startswith("Temps : plus long pour les bluffs (12 s") for t in tells), tells)
        self.assertTrue(any("une overcard tombe (2/2)" in t for t in tells), tells)
        value_only = field.Line("river", "x", "", [mk("value", 100.0, 3.0, "brick") for _ in range(12)])
        self.assertEqual(field.verdict(value_only).kind, "moins")  # au plus 18 % de bluffs, il en faut 33 %
        few = field.Line("river", "x", "", [mk("value", 100.0, 3.0, "brick")])
        self.assertEqual(field.verdict(few).kind, "")
        self.assertEqual(field.tells(few.bets), [])

    def test_size_tell(self):
        mk = lambda intent, size: field.Bet(None, 0, "V", "river", "x", size, 50.0, None, None, False, intent)  # noqa
        small, big = "petite (≤ 55 %)", "grosse (56–120 %)"
        bets = [mk("bluff", small) for _ in range(3)] + [mk("value", small)] + [mk("value", big) for _ in range(4)]
        text = field.size_tell(field.by_size(bets), "river")
        self.assertEqual(text, "Ses mises petites sont souvent des bluffs (3/4), ses mises grosses rarement (0/4)")


class StyleAndLeaksTest(unittest.TestCase):
    def test_styles(self):
        r = lambda pct, n=100: Ratio(round(pct * n / 100), n)  # noqa: E731
        self.assertEqual(field.style_of({"afq": r(60), "fold_bet": r(30)}, True)[0], "agressif")
        self.assertEqual(field.style_of({"afq": r(20), "fold_bet": r(25)}, True)[0], "passif")
        self.assertEqual(field.style_of({"afq": r(38), "fold_bet": r(70)}, True)[0], "prudent")
        self.assertEqual(field.style_of({"afq": r(40), "fold_bet": r(45), "vpip": r(30)}, True)[0], "autre")
        self.assertEqual(field.style_of({}, True), ("autre", ["pas assez de mains"]))
        self.assertEqual(field.style_of({"afq": r(55), "fold_bet": r(40)}, False)[0], "autre")  # le heads-up : 60 %

    def test_ring_findings(self):
        found = field.ring_findings({"fold_cbet": Ratio(80, 100), "vpip": Ratio(24, 100), "fold_river": Ratio(2, 10)})
        self.assertEqual([f.stat.key for f in found], ["fold_cbet"])  # la river : trop peu d'occasions
        fact, numbers, exploit = field.finding_text(found[0])
        self.assertEqual((fact, numbers), ("Folde trop face à la c-bet", "Fold vs c-bet 80 % sur 100 (repère 35–52 %)"))
        self.assertTrue(found[0].strong)
        self.assertIn("c-bet large et petit", exploit)

    def test_study_ring(self):
        hands = [barrel_hand(k, ["8c", "6c"]) for k in range(20)]
        study = field.study(hands, [CO], "ring")
        self.assertEqual((study.hands, len(study.bets), study.shown), (20, 40, 40))
        self.assertEqual(study.ratios["cbet_turn"].opps, 20)
        self.assertTrue(study.key_lines())
        self.assertEqual(study.preflop, {"Open": [("86s", h) for h in hands]})
        self.assertEqual(study.checks["river"], Counter({"bluff": 20}))
        group = field.study(hands, [CO, BB], "ring")  # un groupe : leurs mises réunies
        self.assertEqual(len(group.bets), 40)


def _library(folder: Path, extra: str = "") -> Library:
    shutil.copy(FIXTURE, folder / "sample.txt")
    if extra:
        (folder / "extra.txt").write_text(extra, encoding="utf-8")
    return Library(folder)


def other_hero_copy() -> str:
    """Les mains du fichier de test jouées sous un autre pseudo (« Moi2 »), avec d'autres numéros."""
    text = FIXTURE.read_text(encoding="utf-8")
    text = re.sub(r"Hand ID: HAND(\d+)", r"Hand ID: AUTRE\1", text)
    return re.sub(r"Hero(?!\])", "Moi2", text)


def unmarked_copy() -> str:
    """Les mains du fichier de test jouées par « Ancien » contre « Autre », sans héros marqué par l'historique."""
    text = FIXTURE.read_text(encoding="utf-8")
    text = re.sub(r"Hand ID: HAND(\d+)", r"Hand ID: SANS\1", text)
    return re.sub(r"Hero(?!\])", "Ancien", text).replace(" Hero]", "]").replace("Villain", "Autre")


class SettingsTest(IsolatedHome):
    def test_check_and_save(self):
        self.assertEqual(settings.load(), settings.DEFAULTS)
        saved = settings.save({"formats": ["ring", "HU"], "min_hands": 80})
        self.assertEqual((saved["formats"], saved["min_hands"]), (["HU", "ring"], 80))
        for bad in ({"formats": []}, {"formats": ["5max"]}, {"min_hands": 5}, {"coach": "oui"}, {"inconnu": 1}, [],
                    {"hero": ""}, {"hero": "x" * 61}, {"hero_added": "Moi2"}, {"hero_excluded": [""]}):
            with self.assertRaises(ValueError):
                settings.save(bad)
        self.assertEqual(settings.enabled_formats(["HU", "ring"], ["ring"]), ["ring"])
        self.assertEqual(settings.enabled_formats(["HU"], ["ring"]), ["HU"])  # sans mains de tables à plusieurs
        self.assertTrue(settings.coach_shown(2, None))
        self.assertFalse(settings.coach_shown(2, False))
        self.assertFalse(settings.coach_shown(0, None))

    def test_pseudos_grouped(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        (folder / "sans-heros.txt").write_text(unmarked_copy(), encoding="utf-8")
        lib = _library(folder, other_hero_copy())
        self.addCleanup(lib.solves.shutdown)
        view = lib.settings_view()
        self.assertEqual([(p["name"], p["included"]) for p in view["pseudos"]], [("Hero", True), ("Moi2", True)])
        self.assertEqual(len(lib.hands), 8)  # tes deux pseudos réunis en un seul joueur
        self.assertEqual(set(view["players"]), {"Ancien", "Autre"})  # à ajouter : les joueurs des mains sans héros
        state = lib.set_settings({"hero": "MonNom"})  # le regroupement renommé
        self.assertEqual((state["hero"], lib.hero, len(lib.hands)), ("MonNom", "MonNom", 8))
        self.assertTrue(all(h.hero == "MonNom" for h in lib.hands))
        with self.assertRaises(ValueError):
            lib.set_settings({"hero": "Villain"})  # le pseudo d'un adversaire
        lib.set_settings({"hero_excluded": ["Moi2"]})  # pas toi : ses mains sortent de tes analyses
        self.assertEqual((lib.hero, len(lib.hands)), ("MonNom", 4))
        self.assertEqual([p["included"] for p in lib.settings_view()["pseudos"]], [True, False])
        with self.assertRaises(ValueError):
            lib.set_settings({"hero_excluded": ["Hero", "Moi2"]})  # il reste au moins un pseudo
        with self.assertRaises(ValueError):
            lib.set_settings({"hero_added": ["Villain"]})  # il joue contre toi : pas un de tes pseudos
        with self.assertRaises(ValueError):
            lib.set_settings({"hero_added": ["Personne"]})  # dans aucune main
        lib.set_settings({"hero_added": ["Ancien", "Moi2"]})  # « Moi2 » se recoche, « Ancien » s'ajoute
        conf = settings.load()
        self.assertEqual((conf["hero_added"], conf["hero_excluded"]), (["Ancien"], []))
        self.assertEqual(len(lib.hands), 12)
        self.assertEqual([(p["name"], p["added"], p["hands"]) for p in lib.settings_view()["pseudos"]][-1],
                         ("Ancien", True, 4))
        lib.set_settings({"hero": "Moi2"})
        lib.set_settings({"hero_excluded": ["Moi2"]})  # le nom était ce pseudo : le plus fréquent le remplace
        self.assertIsNone(settings.load()["hero"])
        self.assertIn(lib.hero, ("Hero", "Ancien"))
        self.assertEqual(len(lib.hands), 8)
        page = lib.settings_page()
        self.assertIn("Pseudos réunis", page)
        self.assertIn('class="st-pseudo" value="Moi2">', page)  # décoché
        self.assertIn('class="st-pseudo" value="Ancien" data-added checked>', page)

    def test_library(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib = _library(Path(tmp.name), other_hero_copy())
        self.addCleanup(lib.solves.shutdown)
        state = lib.set_settings({"formats": ["ring"]})
        self.assertEqual(state["settings"]["plays"], ["ring"])
        self.assertEqual([f for f, _ in lib.leak_formats()], ["HU"])  # pas de mains à plusieurs : le heads-up reste
        state = lib.set_settings({"coach": False, "min_hands": 10})
        self.assertEqual((state["settings"]["coach"], state["settings"]["min_hands"]), (False, 10))
        page = lib.settings_page()
        self.assertIn("Nom du regroupement", page)
        if shutil.which("node"):  # le script de la page doit au moins être du JavaScript valide
            script = Path(tempfile.mkdtemp(dir=self.home.parent)) / "parametres.js"
            script.write_text(page.rsplit("<script>", 1)[1].split("</script>")[0], encoding="utf-8")
            self.assertEqual(subprocess.run(["node", "--check", str(script)], capture_output=True).returncode, 0)


class FieldLibraryTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lib = _library(Path(tmp.name))
        self.addCleanup(self.lib.solves.shutdown)
        ring = [barrel_hand(k, ["8c", "6c"] if k % 3 else ["Kh", "Qh"]) for k in range(60)]
        with self.lib._lock:  # des mains de tables à plusieurs, jouées par « Hero »
            self.lib._all_ring = ring
            self.lib._select()

    def test_lists_player_and_group(self):
        lib = self.lib
        settings.save({"min_hands": 50})
        regs = lib.field_page("regs", "ring")
        self.assertIn("Joueur par joueur", regs)
        self.assertIn(f'href="/field/joueur/{CO}?format=ring"', regs)
        self.assertIn("Analyse détaillée", regs)
        players.set_kind(BB, "rec")
        recs = lib.field_page("recs", "ring")
        self.assertIn(f">{BB}</a>", recs)
        self.assertIn("Par style", recs)
        page = lib.field_player(CO, "ring")
        for text in ("Ses leaks à exploiter", "Ce qui distingue sa value de ses bluffs", "Ses lignes",
                     "Barrel (a misé le flop)", "Quand il checke", "Ses mains montrées avant le flop", "Open"):
            self.assertIn(text, page)
        style = lib.field_study("ring", (BB,)).style[0]
        group = lib.field_group(style, "ring")
        self.assertIn("Les joueurs du groupe", group)
        self.assertIn("Comment les jouer", group)
        with self.assertRaises(KeyError):
            lib.field_group("inconnu", "ring")
        settings.save({"min_hands": 100})  # au-dessus de leurs mains : plus personne n'est étudié
        with lib._lock:
            lib._select()
        self.assertIn("Aucun régulier avec au moins 100 mains", lib.field_page("regs", "ring"))

    def test_heads_up_player(self):
        page = self.lib.field_player("Villain", "HU")
        self.assertIn("Villain en heads-up", page)
        self.assertIn('/#/adversaire/Villain/plan', page)  # sa fiche heads-up
        self.assertIn('nav class="lk-fmt"', self.lib.field_page("regs", "HU"))  # le choix de format


class RoutesTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        library = _library(Path(tmp.name))
        self.addCleanup(library.solves.shutdown)
        self.server = start(library, port=0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(self, method, path, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=30)
        body = json.dumps(payload) if payload is not None else None
        conn.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_routes(self):
        for path in ("/field/regs", "/field/recs", "/field/regs?format=HU", "/field/joueur/Villain", "/parametres/general",
                     "/parametres/joueurs", "/field/joueurs"):
            self.assertEqual(self.request("GET", path)[0], 200, path)
        self.assertEqual(self.request("GET", "/field/joueur/Inconnu")[0], 404)
        self.assertEqual(self.request("GET", "/field/groupe/inconnu")[0], 404)
        status, data = self.request("POST", "/api/parametres", {"min_hands": 20})
        self.assertEqual((status, json.loads(data)["settings"]["min_hands"]), (200, 20))
        self.assertEqual(self.request("POST", "/api/parametres", {"formats": []})[0], 400)
        state = json.loads(self.request("GET", "/api/state")[1])
        self.assertEqual(state["settings"]["plays"], None)
        self.assertIn("ring_opponents", state)


if __name__ == "__main__":
    unittest.main()

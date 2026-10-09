"""Une grosse base de mains reste fluide : le ramasse-miettes en pause pendant les chargements, les mains lues gardées
par blocs dans le cache, un import qui n'ajoute que ses mains, les lectures gardées sur chaque main, les pages
préchargées en arrière-plan."""
import gc
import pickle
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from analyzer import memory, ring, store
from analyzer.app.library import Library
from analyzer.db import hands as db_hands
from analyzer.models import CALL, FOLD, RAISE, Action, Seat, hand_from_dict, hand_to_dict
from analyzer.parsers import load_hands
from analyzer.stats import HandReader
from tests.base import IsolatedHome, isolate_module, release_module
from tests.test_ring_leaks import FOLDS_TO, table_hand


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()


FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"


def renumbered(text: str, digit: str) -> str:
    """Le même historique avec d'autres numéros de main (HAND01 -> HAND<digit>1…)."""
    return text.replace("Hand ID: HAND0", f"Hand ID: HAND{digit}")


class MemoryTest(unittest.TestCase):
    def test_loading(self):
        was = gc.isenabled()
        gc.enable()
        try:
            with memory.loading() as outer:
                self.assertFalse(gc.isenabled())  # pas de passage pendant le chargement
                with memory.loading():
                    pass
                self.assertFalse(gc.isenabled())  # jusqu'à la fin du dernier
                self.assertTrue(outer.release)
            self.assertTrue(gc.isenabled())
            self.assertGreater(gc.get_freeze_count(), 0)  # ce qui est en mémoire, à l'écart de ses passages
        finally:
            gc.unfreeze()
            if not was:
                gc.disable()


class ModelTest(unittest.TestCase):
    def test_compact_objects(self):
        hands = load_hands([FIXTURE])
        hand = hands[0]
        hand.__dict__["_ring_reads"] = {"Hero": {}}  # une lecture gardée sur la main
        back = pickle.loads(pickle.dumps(hands))
        self.assertEqual(back, hands)
        self.assertNotIn("_ring_reads", back[0].__dict__)  # le cache des mains ne garde que la main
        self.assertFalse(hasattr(Action("p", FOLD, "preflop", 0.0, 0.0), "__dict__"))  # slots : plus petits
        self.assertFalse(hasattr(Seat("p", 1, 100.0), "__dict__"))
        rebuilt = [hand_from_dict(hand_to_dict(h)) for h in hands]  # relues de la base : les textes répétés partagés
        self.assertEqual(rebuilt, hands)
        names = {id(a.player) for h in rebuilt for a in h.actions if a.player == "Hero"}
        names |= {id(name) for h in rebuilt for name in h.seats if name == "Hero"}
        self.assertEqual(len(names), 1)  # un seul texte « Hero » pour toutes les mains


class RingReadTest(unittest.TestCase):
    def test_read_all(self):
        """Le CO ouvre, le bouton 3bet, tout le monde se couche, le CO aussi : chacun lu en un seul passage."""
        hand = table_hand(1, "CO", ["Ah", "Kd"], FOLDS_TO["CO"] + [
            ("CO", RAISE, 5.0), ("BTN", RAISE, 15.0), ("SB", FOLD), ("BB", FOLD), ("CO", FOLD)], winner="BTN", board=())
        every = ring.read_all(hand)
        names = {hand.position(p): p for p in hand.seats}
        self.assertEqual(every[names["CO"]],
                         {"open": True, "limp": False, "fold_3bet": True, "vpip": True, "pfr": True})
        self.assertEqual(every[names["BTN"]], {"threebet": True, "flat": False, "vpip": True, "pfr": True})
        self.assertEqual(every[names["UTG"]], {"open": False, "limp": False, "vpip": False, "pfr": False})
        self.assertEqual(every[names["BB"]], {"vpip": False, "pfr": False})  # 3bet en face : ni défense, ni vol
        steal = table_hand(2, "BB", ["9h", "8h"], FOLDS_TO["BTN"] + [("BTN", RAISE, 5.0), ("SB", FOLD), ("BB", CALL)],
                           post=[("flop", "BB", "check"), ("flop", "BTN", "bet", 3.0), ("flop", "BB", FOLD)],
                           winner="BTN")
        every = ring.read_all(steal)
        bb = next(p for p in steal.seats if steal.position(p) == "BB")
        btn = next(p for p in steal.seats if steal.position(p) == "BTN")
        self.assertEqual(every[bb], {"threebet": False, "flat": True, "fold_steal": False, "threebet_steal": False,
                                     "vpip": True, "pfr": False, "fold_cbet": True, "wtsd": False})
        self.assertEqual(every[btn]["cbet_hu"], True)
        self.assertIs(ring.read(steal, bb), ring.read(steal, bb))  # gardée sur la main
        self.assertEqual(ring.read(steal, bb), every[bb])


class HandCacheTest(IsolatedHome):
    def test_blocks(self):
        """Les mains lues sont gardées par blocs de lignes ; un import ne refait que les blocs qui changent."""
        conn = Library(self.home / "vide").db
        space = db_hands.space(conn, "moi", "Moi")
        text = FIXTURE.read_text(encoding="utf-8")
        db_hands.import_text(conn, space, "a.txt", text)
        with mock.patch.object(db_hands, "CACHE_BLOCK", 2):  # lignes 1 | 2, 3 | 4
            first = db_hands.load(conn, space)
            self.assertEqual(len(first), 4)
            with mock.patch.object(db_hands, "_unpack", side_effect=AssertionError("relu depuis la base")):
                self.assertEqual([h.hand_id for h in db_hands.load(conn, space)], [h.hand_id for h in first])
            db_hands.import_text(conn, space, "b.txt", renumbered(text, "1"))  # lignes 5 à 8
            calls = []
            real = db_hands._unpack
            with mock.patch.object(db_hands, "_unpack", side_effect=lambda blob: calls.append(1) or real(blob)):
                again = db_hands.load(conn, space)
            self.assertEqual(len(again), 8)
            self.assertEqual(len(calls), 5)  # le bloc 4 | 5 (changé) et les nouveaux : 6, 7 | 8
            prefix = f"{conn.url}|{space}|"
            kept = [r[0] for r in store._connect().execute(
                "SELECT cle FROM cache WHERE espace = 'mains' AND substr(cle, 1, ?) = ?", (len(prefix), prefix))]
            self.assertEqual(len(kept), 5)  # les blocs d'avant (4 seul) sont effacés

    def test_load_after(self):
        conn = Library(self.home / "vide").db
        space = db_hands.space(conn, "moi", "Moi")
        text = FIXTURE.read_text(encoding="utf-8")
        db_hands.import_text(conn, space, "a.txt", text)
        seen = db_hands.rows(conn, space)
        self.assertEqual(db_hands.load_after(conn, space, seen), ([], seen))
        db_hands.import_text(conn, space, "b.txt", renumbered(text, "1"))
        new, rows = db_hands.load_after(conn, space, seen)
        self.assertEqual(([h.hand_id for h in new], rows[0]), (["HAND14", "HAND13", "HAND12", "HAND11"], 8))
        file_id = conn.value("SELECT id FROM fichiers WHERE nom = 'a.txt'")
        db_hands.remove_file(conn, space, file_id)
        self.assertIsNone(db_hands.load_after(conn, space, rows))  # des mains retirées : tout relire


class IncrementalImportTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.text = FIXTURE.read_text(encoding="utf-8")

    def test_new_hands_only(self):
        lib = Library(self.folder)
        lib.import_files([{"name": "a.txt", "content": self.text}])
        kept = lib.by_id["HAND01"]
        reader = HandReader.of(kept)  # une lecture gardée sur la main
        with mock.patch.object(lib, "_load", wraps=lib._load) as full:
            result = lib.import_files([{"name": "b.txt", "content": renumbered(self.text, "1")}])
        full.assert_not_called()  # seulement les mains ajoutées
        self.assertEqual((result["added"], result["state"]["hands"]), (4, 8))
        self.assertIs(lib.by_id["HAND01"], kept)
        self.assertIs(HandReader.of(lib.by_id["HAND01"]), reader)
        fresh = Library(self.folder)  # tout relu : la même chose
        self.assertEqual([h.hand_id for h in fresh.hands], [h.hand_id for h in lib.hands])
        self.assertEqual((fresh.hero, dict(fresh.hero_pseudos), dict(fresh.players_seen)),
                         (lib.hero, dict(lib.hero_pseudos), dict(lib.players_seen)))
        self.assertEqual(fresh.summary()["opponents"], lib.summary()["opponents"])

    def test_full_reload_when_needed(self):
        lib = Library(self.folder)
        lib.import_files([{"name": "a.txt", "content": self.text}])
        lib.import_files([{"name": "b.txt", "content": renumbered(self.text, "1")}])
        file_id = lib.db.value("SELECT id FROM fichiers WHERE nom = 'a.txt'")
        lib.remove_file(file_id)  # des mains retirées : tout est relu
        self.assertEqual(sorted(lib.by_id), ["HAND11", "HAND12", "HAND13", "HAND14"])
        with mock.patch("analyzer.aliases.load", return_value={"Villain": "Le Vilain"}), \
                mock.patch.object(lib, "_load", wraps=lib._load) as full:  # un alias de plus : tout relire
            lib.import_files([{"name": "c.txt", "content": renumbered(self.text, "2")}])
        full.assert_called_once()


class WarmUpTest(IsolatedHome):
    def test_pages_ready(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        shutil.copy(FIXTURE, folder / "sample.txt")
        lib = Library(folder, warm_up=True)
        deadline = time.time() + 60
        while any(t.name == "prechargement" for t in threading.enumerate()) and time.time() < deadline:
            time.sleep(0.05)
        built = {key[1:3] for key in lib._cache}
        for wanted in (("self", "bilan"), ("self", "preflop"), ("self", "mains"), ("self", "spots")):
            self.assertIn(wanted, built)
        self.assertTrue(any(key[1] == "leaks" for key in lib._cache))
        self.assertEqual(lib._busy, 0)
        self.assertFalse(Library(folder).warm_up)  # jamais sans le demander (les tests, la ligne de commande)


if __name__ == "__main__":
    unittest.main()

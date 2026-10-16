import json
import shutil
import tempfile
import unittest
from pathlib import Path

from analyzer import replay
from analyzer.app import catch_trainer
from analyzer.app.library import Library
from analyzer.errors import NotFound
from analyzer.models import CALL
from analyzer.parsers import load_hands

try:
    from .base import IsolatedHome, isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome, isolate_module, release_module

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures" / "betclic_sample.txt"


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()


class RecordTest(unittest.TestCase):
    def test_ring_showdown(self):
        hand = next(h for h in load_hands([HERE / "sites" / "ggpoker.txt"]) if h.hand_id == "HD100000001")
        rec = replay.record(hand, "Hero")
        self.assertEqual(([p["pos"] for p in rec["players"]], rec["format"], rec["stakes"]),
                         (["UTG", "HJ", "CO", "BTN", "SB", "BB"], "6-max", "NL5000"))
        hero = next(p for p in rec["players"] if p["hero"])
        self.assertEqual((hero["name"], hero["cards"], hero["net"], hero["button"]), ("Hero", ["2h", "9h"], 0.0, True))
        shown = {p["name"]: p["cards"] for p in rec["players"] if p["cards"] and not p["hero"]}
        self.assertEqual(shown, {"e1f2a3b4": ["Kc", "Kh"], "a3b4c5d6": ["5d", "3s"]})
        self.assertEqual(len(rec["board"]), 5)
        self.assertEqual(rec["actions"][0], {"s": "preflop", "p": 4, "k": "post_sb", "a": 0.5, "to": 0.5, "pot": 0.0,
                                             "ai": False})
        self.assertEqual(rec["win"], {2: 8.74})
        self.assertAlmostEqual(sum(p["net"] for p in rec["players"]), -rec["rake"], places=1)
        json.dumps(rec)  # prêt pour la page

    def test_links(self):
        self.assertEqual(replay.href("A/1"), "/rejouer/A%2F1")
        self.assertEqual(replay.href("X", "/api/eleves/paul"), "/rejouer/X?api=/api/eleves/paul")
        self.assertEqual([replay.api_of(p) for p in ("/moi", "/eleve/paul", "/reference/ana")],
                         ["/api", "/api/eleves/paul", "/api/references/ana"])


class LibraryTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        shutil.copy(FIXTURE, Path(tmp.name) / "hu.txt")
        self.lib = Library(tmp.name)

    def test_preflop_hand(self):
        rec = self.lib.replay_record("HAND04")  # finie au préflop
        self.assertEqual((rec["board"], rec["explorer"], rec["format"]), ([], False, "HU"))
        self.assertEqual([a["k"] for a in rec["actions"]], ["post_sb", "post_bb", "call", "raise", "fold"])
        self.assertTrue(self.lib.replay_record("HAND03")["explorer"])
        with self.assertRaises(NotFound):
            self.lib.replay_record("inconnue")

    def test_student_hand_from_my_space(self):
        ident = self.lib.create_student("Paul")["id"]
        student = self.lib.student(ident)
        Path(student.folder).mkdir(parents=True, exist_ok=True)
        shutil.copy(HERE / "sites" / "ggpoker.txt", Path(student.folder) / "gg.txt")
        student.reload()
        self.assertEqual(student.replay_record("HD100000002")["id"], "HD100000002")
        self.assertEqual(self.lib.replay_record("HD100000002")["id"], "HD100000002")  # cherchée chez tes élèves aussi

    def test_catch_trainer(self):
        data = catch_trainer.exercises([("HU", self.lib.hands, self.lib.kinds())], "Hero")
        self.assertEqual(len(data["exercises"]), 1)
        e = data["exercises"][0]
        hand = self.lib.by_id[e["hand"]]
        self.assertEqual((hand.actions[e["stop"]].player, hand.actions[e["stop"]].kind), ("Hero", CALL))
        self.assertEqual((e["reply"], e["intent"], e["won"], e["kind"], e["replay"]),
                         ("call", "bluff", True, "reg", f"/rejouer/{e['hand']}"))
        self.assertEqual(e["plan"]["title"], "Mise après check au turn, petite (≤ 40 %)")
        self.assertIn("Pas assez de mises vues", e["plan"]["advice"])
        self.assertEqual(data["lines"], [["Mise après check au turn", 1]])
        page = self.lib.catch_trainer_page()
        for text in ("Bluffcatch : tes mains", 'id="bc-data"', "Ce que tu as fait", "Payer"):
            self.assertIn(text, page)
        self.assertIn("Aucune mise river", catch_trainer.build_page(catch_trainer.exercises([], "Hero")))


if __name__ == "__main__":
    unittest.main()

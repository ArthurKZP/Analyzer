import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer import cards, parsers, store
from analyzer.cards import DECK, evaluate, parse_card, score
from analyzer.parsers import load_hands
from analyzer.theory import review

FIXTURES = Path(__file__).parent / "fixtures"


class TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(self.home), "ANALYZER_CACHE": "1"})
        self.env.start()

    def tearDown(self):
        store.close()  # avant d'effacer le dossier (Windows ne supprime pas un fichier ouvert)
        self.env.stop()
        self.tmp.cleanup()


class StoreTest(TempHome):
    def test_roundtrip_versions_and_retain(self):
        self.assertIsNone(store.get("equite", "k"))
        store.put("equite", "k", 0.25)
        store.put("equite", "autre", 0.5)
        store.flush()
        self.assertTrue(store.path().is_file())
        self.assertEqual(store.path(), self.home / "cache" / "analyses.sqlite")
        store._loaded.clear()  # relu depuis le disque
        self.assertEqual(store.get("equite", "k"), 0.25)
        self.assertEqual(store.retain("equite", lambda key: key == "k"), 1)
        self.assertIsNone(store.get("equite", "autre"))
        with mock.patch.dict(store.VERSIONS, {"equite": 99}):  # nouvelle façon de calculer : on oublie
            store._loaded.clear()
            self.assertIsNone(store.get("equite", "k"))
        store.put("mains", "fichier", ("stamp", [1, 2]))  # espace lu clé par clé, en pickle
        self.assertEqual(store.get("mains", "fichier"), ("stamp", [1, 2]))
        with mock.patch.dict(os.environ, {"ANALYZER_CACHE": "0"}):
            self.assertIsNone(store.get("mains", "fichier"))

    def test_parsed_hands_are_cached(self):
        folder = self.home / "mains"
        folder.mkdir()
        target = folder / "sample.txt"
        shutil.copy(FIXTURES / "betclic_sample.txt", target)
        first = load_hands([folder])
        with mock.patch.object(parsers, "parse_text", side_effect=AssertionError("relu")):
            again = load_hands([folder])  # inchangé : rien n'est relu
        self.assertEqual([h.hand_id for h in again], [h.hand_id for h in first])
        self.assertEqual(again[0].actions, first[0].actions)
        text = target.read_text(encoding="utf-8")
        target.write_text(text.replace("HAND01", "HAND91"), encoding="utf-8")
        os.utime(target, ns=(target.stat().st_atime_ns, target.stat().st_mtime_ns + 10**9))
        self.assertIn("HAND91", [h.hand_id for h in load_hands([folder])])  # modifié : relu

    def test_equity_is_remembered(self):
        cards._EQUITY_CACHE.clear()
        value = cards.equity(["7h", "8h"], ["Ac", "Kd"], ["9h", "Th", "2c"])
        with mock.patch.object(cards, "_equity", side_effect=AssertionError("recalculée")):
            self.assertEqual(cards.equity(["7h", "8h"], ["Ac", "Kd"], ["9h", "Th", "2c"]), value)
            store.flush()
            cards._EQUITY_CACHE.clear()
            store._loaded.clear()
            self.assertEqual(cards.equity(["7h", "8h"], ["Ac", "Kd"], ["9h", "Th", "2c"]), value)  # sur disque


class EvaluatorTest(unittest.TestCase):
    def test_score_orders_like_evaluate(self):
        rng = random.Random(7)
        hands = [rng.sample(DECK, 7) for _ in range(4000)]
        for suit in "cdhs":  # couleurs et quintes flush, rares au hasard
            suited = [c for c in DECK if c[1] == suit]
            hands += [rng.sample(suited, 5) + rng.sample([c for c in DECK if c[1] != suit], 2) for _ in range(200)]
        hands += [[r + s for s in "cdhs"] + rng.sample([c for c in DECK if c[0] != r], 3) for r in "23456789TJQKA"]
        values = [(evaluate([parse_card(c) for c in h]), score(h)) for h in hands]
        for (e1, s1), (e2, s2) in zip(values, values[1:] + values[:1]):
            self.assertEqual((e1 > e2) - (e1 < e2), (s1 > s2) - (s1 < s2))

    def test_equity_unchanged(self):
        # les mêmes valeurs qu'avec l'évaluateur d'avant (mêmes tirages Monte-Carlo)
        self.assertEqual(cards._equity(["Ah", "Ad"], ["Kc", "Ks"], [], 20000, ""), 0.81425)
        self.assertAlmostEqual(cards._equity(["7h", "8h"], ["Ac", "Kd"], ["9h", "Th", "2c"], 20000, ""),
                               0.6878787878787879)
        self.assertEqual(cards._equity(["5c", "5d"], ["Ah", "Kh"], ["5h", "Qh", "2h", "Jd", "9c"], 20000, ""), 0.0)


class SpotKeysTest(TempHome):
    def test_collect_builds_each_spot_once(self):
        hands = load_hands([FIXTURES / "betclic_sample.txt"])
        review._KEYS.clear()
        done, todo = review.collect(hands, "Hero")
        self.assertEqual((len(done), len(todo), todo.pot_types), (0, 2, ["pot 3bet", "SRP"]))
        self.assertEqual([s.hand.hand_id for s in todo], todo.hand_ids)  # spots construits à la demande
        self.assertEqual(len(todo[:1]), 1)
        with mock.patch.object(review.postflop, "build_spot", side_effect=AssertionError("reconstruit")):
            self.assertEqual(len(review.collect(hands, "Hero")[1]), 2)  # clés gardées
            review._KEYS.clear()
            self.assertEqual(len(review.collect(hands, "Hero")[1]), 2)  # … et sur disque
        from analyzer.theory import studyspots
        studyspots.save_selection("srp", "KsKd4c", {})  # un réglage change : on recalcule
        calls = []
        real = review.postflop.build_spot
        with mock.patch.object(review.postflop, "build_spot", side_effect=lambda *a, **k: calls.append(1) or real(*a, **k)):
            review.collect(hands, "Hero")
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()

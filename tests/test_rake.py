"""Le rake payé : ta part du rake de chaque pot (au prorata de ce que tu y as mis), en monnaie et en bb/100, dans les
bilans."""
import shutil
import tempfile
import unittest
from pathlib import Path

from analyzer import rake
from analyzer.app import bilan_page
from analyzer.models import CALL, FOLD, RAISE
from analyzer.parsers import parse_text
from analyzer.stats import analyze
from tests.base import IsolatedHome
from tests.test_ring_leaks import FOLDS_TO, table_hand

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"


def sample():
    return parse_text(FIXTURE.read_text(encoding="utf-8"))


class PaidTest(unittest.TestCase):
    def test_shares(self):
        by_id = {h.hand_id: h for h in sample()}
        h = by_id["HAND01"]  # rake 1,50 € ; Hero a mis 20,50 €, Villain 42,50 €
        self.assertAlmostEqual(rake.paid(h, "Hero"), 1.5 * 20.5 / 63)
        self.assertAlmostEqual(rake.paid(h, "Hero") + rake.paid(h, "Villain"), h.rake)
        self.assertEqual(rake.paid(by_id["HAND02"], "Hero"), 1.5)  # pot partagé à parts égales
        self.assertEqual(rake.paid(by_id["HAND04"], "Hero"), 0.0)  # pas de rake
        self.assertEqual(rake.paid(h, "Personne"), 0.0)

    def test_uncalled_and_dead_blinds(self):
        # 6-max à 1/2 € : le CO ouvre à 5, la BB paie ; la SB couchée laisse sa blinde au pot (elle en paie sa part)
        pre = FOLDS_TO["CO"] + [("CO", RAISE, 5.0), ("BTN", FOLD), ("SB", FOLD), ("BB", CALL)]
        h = table_hand(1, "CO", ["Ah", "Kh"], pre)
        h.rake = 0.55
        shares = {p: rake.paid(h, p) for p in h.seats}
        self.assertAlmostEqual(sum(shares.values()), 0.55)
        self.assertAlmostEqual(shares["Hero"], 0.55 * 5 / 11)  # 5 + 5 + la SB morte (1)
        self.assertAlmostEqual(shares["Joueur5"], 0.05)
        h.uncalled["Hero"] = 3.0  # une mise non payée, rendue : elle ne compte pas
        self.assertAlmostEqual(rake.paid(h, "Hero"), 0.55 * 2 / 8)


class BilanTest(IsolatedHome):  # (analyze garde ses équités en cache : dans un dossier à part)
    def test_stats_and_results(self):
        hands = sample()
        expected = sum(rake.paid(h, "Hero") for h in hands)
        st = analyze(hands)["Hero"]
        self.assertAlmostEqual(st.rake, expected)
        self.assertAlmostEqual(st.rake_bb, expected / 5)  # 2,50/5 € : tout en bb de 5 €
        self.assertAlmostEqual(st.rake_per_100, 100 * expected / 5 / 4)
        res = bilan_page.results(hands, "Hero")
        self.assertAlmostEqual(res["rake"], expected)
        self.assertAlmostEqual(res["rake100"], st.rake_per_100)
        tiles = bilan_page.result_tiles(res)
        self.assertIn("Rake payé", tiles)
        self.assertIn("3,24 €", tiles)
        self.assertIn("16,2 bb/100 · avant rake", tiles)
        both = bilan_page.overall_html(res, res)
        self.assertIn("rake 6,48 € (16,2 bb/100)", both)  # tous formats confondus


class LibraryTest(IsolatedHome):
    def test_bilan_pages(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        shutil.copy(FIXTURE, Path(tmp.name) / "sample.txt")
        from analyzer.app.library import Library
        lib = Library(Path(tmp.name))
        self.addCleanup(lib.solves.shutdown)
        page = lib.self_page("bilan")
        self.assertIn("Rake payé", page)
        self.assertIn("3,24 €", page)
        self.assertIn("au prorata de ce que tu y as mis", page)


if __name__ == "__main__":
    unittest.main()
